#!/usr/bin/env python3
"""
Programmatic OAuth bootstrap for Notion MCP (PKCE + dynamic client registration).

What it does:
1) Discovers OAuth metadata from Notion MCP.
2) Registers a public OAuth client (token_endpoint_auth_method=none).
3) Opens browser for user consent.
4) Captures OAuth callback on localhost.
5) Exchanges code for access/refresh tokens.
6) Persists credentials to .env.

Also supports refresh-only mode for non-interactive token renewal.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

MCP_URL = "https://mcp.notion.com/mcp"
ENV_PATH = Path(__file__).resolve().parent / ".env"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _read_env(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _upsert_env(path: Path, updates: dict[str, str]) -> None:
    lines = _read_env(path)
    found: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            new_lines.append(line)
            continue
        key, _value = line.split("=", 1)
        key = key.strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            found.add(key)
        else:
            new_lines.append(line)

    if new_lines and new_lines[-1].strip() != "":
        new_lines.append("")

    for key, value in updates.items():
        if key not in found:
            new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def _authorization_server_metadata(http: httpx.Client) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(MCP_URL)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    # Notion publishes protected resource metadata at the origin-level well-known URL.
    # Keep a fallback to the MCP-path variant for compatibility with other servers.
    candidates = [
        f"{origin}/.well-known/oauth-protected-resource",
        f"{MCP_URL}/.well-known/oauth-protected-resource",
    ]
    pr = None
    last_exc: Exception | None = None
    for url in candidates:
        try:
            pr = http.get(url, timeout=30)
            pr.raise_for_status()
            break
        except Exception as exc:
            last_exc = exc
            pr = None
    if pr is None:
        raise RuntimeError(f"Failed OAuth protected-resource discovery: {last_exc}")

    pr_data = pr.json()
    auth_servers = pr_data.get("authorization_servers") or []
    if not auth_servers:
        raise RuntimeError("No authorization_servers found in protected resource metadata.")

    auth_server = auth_servers[0].rstrip("/")
    metadata_url = f"{auth_server}/.well-known/oauth-authorization-server"
    meta_resp = http.get(metadata_url, timeout=30)
    meta_resp.raise_for_status()
    meta = meta_resp.json()

    required = ("authorization_endpoint", "token_endpoint", "registration_endpoint")
    missing = [k for k in required if not meta.get(k)]
    if missing:
        raise RuntimeError(f"Missing required OAuth metadata fields: {', '.join(missing)}")
    return meta


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    # Shared mutable state injected before server starts.
    shared: dict[str, Any] = {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        self.shared["path"] = parsed.path
        self.shared["params"] = {k: v[0] for k, v in query.items() if v}
        self.shared["received"] = True

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h2>Authorization received.</h2>"
            b"<p>You can close this tab and return to your terminal.</p></body></html>"
        )

    def log_message(self, _format: str, *_args: object) -> None:
        # Keep terminal output clean.
        return


def _run_local_callback_server(port: int, timeout_s: int) -> dict[str, Any]:
    shared: dict[str, Any] = {"received": False, "params": {}, "path": ""}
    _OAuthCallbackHandler.shared = shared

    server = ThreadingHTTPServer(("127.0.0.1", port), _OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline:
            if shared["received"]:
                return shared
            time.sleep(0.15)
        raise TimeoutError("Timed out waiting for OAuth callback.")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _register_client(http: httpx.Client, metadata: dict[str, Any], redirect_uri: str) -> dict[str, Any]:
    payload = {
        "client_name": "notion-mcp-sync-cli",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    resp = http.post(metadata["registration_endpoint"], json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _exchange_code(
    http: httpx.Client,
    metadata: dict[str, Any],
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    resp = http.post(
        metadata["token_endpoint"],
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _refresh_token(http: httpx.Client, metadata: dict[str, Any], refresh_token: str, client_id: str) -> dict[str, Any]:
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    resp = http.post(
        metadata["token_endpoint"],
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def run_auth(port: int, timeout_s: int, no_browser: bool) -> None:
    with httpx.Client() as http:
        metadata = _authorization_server_metadata(http)

        redirect_uri = f"http://127.0.0.1:{port}/callback"
        registration = _register_client(http, metadata, redirect_uri)
        client_id = registration["client_id"]

        code_verifier, code_challenge = _pkce_pair()
        state = secrets.token_urlsafe(32)
        auth_params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "consent",
        }
        auth_url = f"{metadata['authorization_endpoint']}?{urllib.parse.urlencode(auth_params)}"

        print("\nOpen this URL to authorize Notion MCP:\n")
        print(auth_url)
        print("")
        if not no_browser:
            webbrowser.open(auth_url)

        callback = _run_local_callback_server(port=port, timeout_s=timeout_s)
        params = callback.get("params", {})
        if params.get("error"):
            raise RuntimeError(
                f"OAuth error: {params.get('error')} - {params.get('error_description', 'unknown')}"
            )
        if params.get("state") != state:
            raise RuntimeError("Invalid OAuth state (possible CSRF).")
        code = params.get("code")
        if not code:
            raise RuntimeError("Missing authorization code in callback.")

        tokens = _exchange_code(
            http=http,
            metadata=metadata,
            code=code,
            code_verifier=code_verifier,
            client_id=client_id,
            redirect_uri=redirect_uri,
        )

    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    expires_in = str(tokens.get("expires_in", ""))
    if not access_token:
        raise RuntimeError("Token response did not include access_token.")

    _upsert_env(
        ENV_PATH,
        {
            "NOTION_MCP_ACCESS_TOKEN": access_token,
            "NOTION_MCP_REFRESH_TOKEN": refresh_token,
            "NOTION_MCP_CLIENT_ID": client_id,
            "NOTION_MCP_TOKEN_EXPIRES_IN": expires_in,
        },
    )

    print("Saved Notion MCP OAuth credentials to .env")
    print("Updated: NOTION_MCP_ACCESS_TOKEN, NOTION_MCP_REFRESH_TOKEN, NOTION_MCP_CLIENT_ID")


def run_refresh() -> None:
    load_dotenv(ENV_PATH)
    refresh_token = os.environ.get("NOTION_MCP_REFRESH_TOKEN", "").strip()
    client_id = os.environ.get("NOTION_MCP_CLIENT_ID", "").strip()
    if not refresh_token or not client_id:
        raise RuntimeError(
            "Missing NOTION_MCP_REFRESH_TOKEN or NOTION_MCP_CLIENT_ID in .env. Run auth mode first."
        )

    with httpx.Client() as http:
        metadata = _authorization_server_metadata(http)
        tokens = _refresh_token(http, metadata, refresh_token, client_id)

    access_token = tokens.get("access_token", "")
    new_refresh_token = tokens.get("refresh_token", refresh_token)
    expires_in = str(tokens.get("expires_in", ""))
    if not access_token:
        raise RuntimeError("Refresh response did not include access_token.")

    _upsert_env(
        ENV_PATH,
        {
            "NOTION_MCP_ACCESS_TOKEN": access_token,
            "NOTION_MCP_REFRESH_TOKEN": new_refresh_token,
            "NOTION_MCP_CLIENT_ID": client_id,
            "NOTION_MCP_TOKEN_EXPIRES_IN": expires_in,
        },
    )
    print("Refreshed and saved NOTION_MCP_ACCESS_TOKEN in .env")


def main() -> None:
    parser = argparse.ArgumentParser(description="Programmatic OAuth token helper for Notion MCP.")
    parser.add_argument("--refresh-only", action="store_true", help="Skip browser auth and refresh token from .env")
    parser.add_argument("--port", type=int, default=8765, help="Local callback port (default: 8765)")
    parser.add_argument("--timeout", type=int, default=180, help="Callback timeout seconds (default: 180)")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open browser; print URL only")
    args = parser.parse_args()

    if args.refresh_only:
        run_refresh()
    else:
        run_auth(port=args.port, timeout_s=args.timeout, no_browser=args.no_browser)


if __name__ == "__main__":
    main()
