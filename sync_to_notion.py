#!/usr/bin/env python3
"""
Sync research claims from a JSON file to Notion via the official Notion MCP
(https://mcp.notion.com). Uses the Python MCP SDK with Streamable HTTP transport;
no Node dependency.

Requires an OAuth access token for Notion MCP. Set NOTION_MCP_ACCESS_TOKEN in .env.
See README and https://developers.notion.com/guides/mcp/build-mcp-client.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import os
import sys
import urllib.parse
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from get_notion_mcp_token import run_auth

# Load .env from project root
load_dotenv(Path(__file__).resolve().parent / ".env")

NOTION_MCP_URL = "https://mcp.notion.com/mcp"
NOTION_MCP_ACCESS_TOKEN = os.environ.get("NOTION_MCP_ACCESS_TOKEN", "").strip()
NOTION_MCP_REFRESH_TOKEN = os.environ.get("NOTION_MCP_REFRESH_TOKEN", "").strip()
NOTION_MCP_CLIENT_ID = os.environ.get("NOTION_MCP_CLIENT_ID", "").strip()
NOTION_PARENT_PAGE_ID = os.environ.get("NOTION_PARENT_PAGE_ID", "").strip()

# Schema (must match README)
DB_TITLE = "Research claims"
PROP_CLAIM = "Claim"
PROP_SOURCE_URL = "Source URL"
PROP_SOURCE_SNIPPET = "Source Snippet"
PROP_TOPIC = "Topic"
PROP_CONFIDENCE = "Confidence"
PROP_CONTRADICTION = "Contradiction"
PROP_FACT_CHECK_NOTES = "Fact-check notes"
CONFIDENCE_OPTIONS = ("High", "Medium", "Low", "Unverified")


class SyncAbort(Exception):
    """Raised instead of sys.exit inside async paths (clean shutdown, no ExceptionGroup noise)."""


# Notion MCP tool names vary; never use update/delete tools for creation.
_PREFERRED_CREATE_DATA_SOURCE_TOOLS = (
    "create-a-data-source",
    "notion-create-data-source",
)
_PREFERRED_CREATE_PAGE_TOOLS = (
    "notion-create-pages",
    "notion-create-page",
    "create-page",
)


def _match_tool_name(tool_names: list[str], preferred: str) -> str | None:
    pl = preferred.lower()
    for n in tool_names:
        if n.lower() == pl:
            return n
    return None


def _pick_create_data_source_tool(tool_names: list[str]) -> str | None:
    """Select the tool that creates a data source (database), not update/delete."""
    for preferred in _PREFERRED_CREATE_DATA_SOURCE_TOOLS:
        hit = _match_tool_name(tool_names, preferred)
        if hit:
            return hit
    # Prefer *data-source* create tools before *create-database* (different arg shapes).
    ds_candidates: list[str] = []
    db_candidates: list[str] = []
    for name in tool_names:
        n = name.lower()
        if "update" in n or "delete" in n:
            continue
        if "create" in n and "data" in n and "source" in n:
            ds_candidates.append(name)
        elif "create" in n and "database" in n:
            db_candidates.append(name)
    if ds_candidates:
        return ds_candidates[0]
    if db_candidates:
        return db_candidates[0]
    return None


def _tool_is_notion_create_database_mcp(tool_name: str) -> bool:
    """
    Hosted MCP `notion-create-database` expects `schema` as SQL DDL `CREATE TABLE (...)`
    (see tool description), plus string `title` and `parent`.

    Other tools (e.g. create-a-data-source) use JSON `properties` instead.
    """
    n = tool_name.lower()
    if "update" in n or "delete" in n:
        return False
    if "data-source" in n or "data_source" in n:
        return False
    if "create" in n and "database" in n:
        return True
    return False


def _title_rich_text() -> list[dict]:
    return [{"type": "text", "text": {"content": DB_TITLE}}]


def _sql_ddl_create_research_claims_table() -> str:
    """
    Hosted MCP `notion-create-database` expects SQL DDL (see tool inputSchema), not JSON.
    Column names are double-quoted; SELECT options use single-quoted 'name':color pairs.
    """
    # Colors are Notion palette names accepted by the MCP SQL parser.
    opts = ", ".join(
        f"'{o}':{c}"
        for o, c in zip(
            CONFIDENCE_OPTIONS,
            ("green", "yellow", "orange", "gray"),
        )
    )
    return (
        "CREATE TABLE ("
        f'"{PROP_CLAIM}" TITLE, '
        f'"{PROP_SOURCE_URL}" URL, '
        f'"{PROP_SOURCE_SNIPPET}" RICH_TEXT, '
        f'"{PROP_TOPIC}" RICH_TEXT, '
        f'"{PROP_CONFIDENCE}" SELECT({opts}), '
        f'"{PROP_CONTRADICTION}" CHECKBOX, '
        f'"{PROP_FACT_CHECK_NOTES}" RICH_TEXT'
        ")"
    )


def _args_notion_create_database_mcp(parent_page_id: str) -> dict:
    """Arguments matching hosted MCP inputSchema for notion-create-database."""
    return {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": DB_TITLE,
        "schema": _sql_ddl_create_research_claims_table(),
    }


def _args_post_create_data_source(parent_page_id: str) -> dict:
    """POST /v1/data_sources: pageIdParentRequest is { page_id } only (see notion-openapi.json)."""
    return {
        "parent": {"page_id": parent_page_id},
        "title": _title_rich_text(),
        "properties": _database_schema(),
    }


def _create_ds_call_args(tool_name: str, parent_page_id: str) -> dict:
    if _tool_is_notion_create_database_mcp(tool_name):
        return _args_notion_create_database_mcp(parent_page_id)
    return _args_post_create_data_source(parent_page_id)


def _pick_create_page_tool(tool_names: list[str]) -> str | None:
    """Select the tool that creates pages, not update/archive."""
    for preferred in _PREFERRED_CREATE_PAGE_TOOLS:
        hit = _match_tool_name(tool_names, preferred)
        if hit:
            return hit
    candidates: list[str] = []
    for name in tool_names:
        n = name.lower()
        if "update" in n or "delete" in n or "archive" in n:
            continue
        if "create" in n and "page" in n:
            candidates.append(name)
    return candidates[0] if candidates else None


def _authorization_server_metadata(http: httpx.Client) -> dict:
    """Discover OAuth authorization server metadata for Notion MCP."""
    parsed = urllib.parse.urlparse(NOTION_MCP_URL)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [
        f"{origin}/.well-known/oauth-protected-resource",
        f"{NOTION_MCP_URL}/.well-known/oauth-protected-resource",
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
        raise RuntimeError("No authorization server found for Notion MCP.")
    auth_server = auth_servers[0].rstrip("/")
    meta_url = f"{auth_server}/.well-known/oauth-authorization-server"
    meta_resp = http.get(meta_url, timeout=30)
    meta_resp.raise_for_status()
    return meta_resp.json()


def _refresh_access_token_if_possible() -> str:
    """
    Refresh access token using refresh token + client ID.

    Returns the new access token, or empty string on failure.
    """
    if not NOTION_MCP_REFRESH_TOKEN or not NOTION_MCP_CLIENT_ID:
        return ""
    try:
        with httpx.Client() as http:
            meta = _authorization_server_metadata(http)
            token_endpoint = meta.get("token_endpoint")
            if not token_endpoint:
                return ""
            form = {
                "grant_type": "refresh_token",
                "refresh_token": NOTION_MCP_REFRESH_TOKEN,
                "client_id": NOTION_MCP_CLIENT_ID,
            }
            resp = http.post(
                token_endpoint,
                data=form,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            new_access = (data.get("access_token") or "").strip()
            new_refresh = (data.get("refresh_token") or "").strip()
            if not new_access:
                return ""

            # Keep env in sync for next runs.
            env_path = Path(__file__).resolve().parent / ".env"
            if env_path.exists():
                env_text = env_path.read_text(encoding="utf-8")
            else:
                env_text = ""

            def upsert(env_raw: str, key: str, value: str) -> str:
                if re.search(rf"(?m)^{re.escape(key)}=", env_raw):
                    return re.sub(rf"(?m)^{re.escape(key)}=.*$", f"{key}={value}", env_raw)
                suffix = "\n" if env_raw and not env_raw.endswith("\n") else ""
                return f"{env_raw}{suffix}{key}={value}\n"

            env_text = upsert(env_text, "NOTION_MCP_ACCESS_TOKEN", new_access)
            if new_refresh:
                env_text = upsert(env_text, "NOTION_MCP_REFRESH_TOKEN", new_refresh)
            env_path.write_text(env_text, encoding="utf-8")
            return new_access
    except Exception:
        return ""


def _claim_to_sqlite_properties(c: dict) -> dict:
    """
    Map one claim to `notion-create-pages` properties: plain strings/numbers (SQLite values).
    """
    confidence = (c.get("confidence") or "Unverified").strip()
    if confidence not in CONFIDENCE_OPTIONS:
        confidence = "Unverified"
    return {
        PROP_CLAIM: (c.get("claim") or "")[:2000],
        PROP_SOURCE_URL: (c.get("source_url") or "").strip(),
        PROP_SOURCE_SNIPPET: (c.get("source_snippet") or "").strip(),
        PROP_TOPIC: (c.get("topic") or "").strip(),
        PROP_CONFIDENCE: confidence,
        PROP_CONTRADICTION: "__YES__" if c.get("contradiction") else "__NO__",
        PROP_FACT_CHECK_NOTES: (c.get("fact_check_notes") or "").strip(),
    }


def _normalize_data_source_id(raw: str) -> str:
    """Strip collection:// and trailing junk; return UUID with dashes when possible."""
    s = (raw or "").strip()
    if s.startswith("collection://"):
        s = s[len("collection://") :]
    s = s.split("}", 1)[0].strip()
    m = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})",
        s,
        re.I,
    )
    if not m:
        return raw.strip()
    u = m.group(1)
    if len(u) == 32 and "-" not in u:
        return f"{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:]}"
    return u


def _database_schema() -> dict:
    """Property schema for the Research claims data source."""
    return {
        PROP_CLAIM: {"title": {}},
        PROP_SOURCE_URL: {"url": {}},
        PROP_SOURCE_SNIPPET: {"rich_text": {}},
        PROP_TOPIC: {"rich_text": {}},
        PROP_CONFIDENCE: {
            "select": {
                "options": [{"name": o} for o in CONFIDENCE_OPTIONS],
            }
        },
        PROP_CONTRADICTION: {"checkbox": {}},
        PROP_FACT_CHECK_NOTES: {"rich_text": {}},
    }


def _normalize_page_id_for_parent(raw: str) -> str:
    """Ensure parent page_id UUID uses dashed form for MCP."""
    s = (raw or "").strip()
    m = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})",
        s,
        re.I,
    )
    if not m:
        return s
    u = m.group(1)
    if len(u) == 32 and "-" not in u:
        return f"{u[:8]}-{u[8:12]}-{u[12:16]}-{u[16:20]}-{u[20:]}"
    return u


async def publish_report_page(
    markdown: str,
    page_title: str,
    *,
    auto_auth: bool = True,
    auth_port: int = 8765,
    auth_timeout: int = 180,
    auth_no_browser: bool = False,
) -> None:
    """
    Create a normal (non-database) page under NOTION_PARENT_PAGE_ID with Notion Markdown body.
    Uses notion-create-pages with parent type page_id.
    """
    def current_access_token() -> str:
        return os.environ.get("NOTION_MCP_ACCESS_TOKEN", "").strip()

    access_token = current_access_token()
    if not access_token:
        access_token = _refresh_access_token_if_possible()
    if not access_token and auto_auth:
        print("No Notion MCP access token found. Starting OAuth flow...")
        run_auth(port=auth_port, timeout_s=auth_timeout, no_browser=auth_no_browser)
        load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
        access_token = current_access_token()
    if not access_token:
        print(
            "Error: Missing token. Run `python get_notion_mcp_token.py` or set NOTION_MCP_ACCESS_TOKEN in .env.",
            file=sys.stderr,
        )
        raise SyncAbort()
    if not NOTION_PARENT_PAGE_ID:
        print(
            "Error: Set NOTION_PARENT_PAGE_ID in .env (parent page for the report).",
            file=sys.stderr,
        )
        raise SyncAbort()

    parent_page_id = _normalize_page_id_for_parent(NOTION_PARENT_PAGE_ID)
    max_attempts = 2
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "notion-mcp-sync/1.0",
        }
        try:
            async with httpx.AsyncClient(headers=headers, timeout=120.0) as http_client:
                async with streamable_http_client(NOTION_MCP_URL, http_client=http_client) as (
                    read_stream,
                    write_stream,
                    _,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools_response = await session.list_tools()
                        tool_names = [t.name for t in tools_response.tools]
                        create_page_name = _pick_create_page_tool(tool_names)
                        if not create_page_name:
                            print(
                                "Error: No create-page tool found. Available:",
                                tool_names,
                                file=sys.stderr,
                            )
                            raise SyncAbort()
                        create_args = {
                            "parent": {
                                "type": "page_id",
                                "page_id": parent_page_id,
                            },
                            "pages": [
                                {
                                    "content": markdown,
                                    "properties": {"title": page_title},
                                }
                            ],
                        }
                        result = await session.call_tool(create_page_name, create_args)
                        if result.isError:
                            print(f"Error publishing report: {result}", file=sys.stderr)
                            raise SyncAbort()
                        print("Published report to Notion (child page under NOTION_PARENT_PAGE_ID).")
                        return
        except SyncAbort:
            raise
        except Exception as e:
            last_error = e
            if "401" in str(e) and attempt < max_attempts - 1:
                refreshed = _refresh_access_token_if_possible()
                if refreshed:
                    print("Access token was unauthorized; refreshed token and retrying once...")
                    access_token = refreshed
                    continue
                if auto_auth:
                    print("Access token unauthorized; starting OAuth flow to get a new token...")
                    run_auth(port=auth_port, timeout_s=auth_timeout, no_browser=auth_no_browser)
                    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
                    access_token = current_access_token()
                    if access_token:
                        continue
            break

    if last_error and "401" in str(last_error):
        print(
            "Error: Notion MCP returned 401 Unauthorized. Token is invalid/expired.\n"
            "Run `python get_notion_mcp_token.py` (or remove --no-auto-auth) and try again.",
            file=sys.stderr,
        )
        raise SyncAbort()
    if last_error:
        raise last_error


async def _run_sync(
    path: Path,
    create_db_only: bool,
    auto_auth: bool,
    auth_port: int,
    auth_timeout: int,
    auth_no_browser: bool,
) -> None:
    """Connect to Notion MCP, create data source and pages."""
    def current_access_token() -> str:
        return os.environ.get("NOTION_MCP_ACCESS_TOKEN", "").strip()

    access_token = current_access_token()
    if not access_token:
        access_token = _refresh_access_token_if_possible()
    if not access_token and auto_auth:
        print("No Notion MCP access token found. Starting OAuth flow...")
        run_auth(port=auth_port, timeout_s=auth_timeout, no_browser=auth_no_browser)
        # Reload from .env after auth bootstrap.
        load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
        access_token = current_access_token()
    if not access_token:
        print(
            "Error: Missing token. Run `python get_notion_mcp_token.py` or set NOTION_MCP_ACCESS_TOKEN in .env.",
            file=sys.stderr,
        )
        raise SyncAbort()
    if not NOTION_PARENT_PAGE_ID:
        print(
            "Error: Set NOTION_PARENT_PAGE_ID in .env (page ID where the database will live).",
            file=sys.stderr,
        )
        raise SyncAbort()

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    claims = data.get("claims", [])
    if not claims:
        print("No claims in file. Exiting.")
        return

    # One retry path for unauthorized tokens:
    # 1) attempt refresh using refresh token
    # 2) if still unauthorized and auto_auth enabled, run interactive OAuth
    max_attempts = 2
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "notion-mcp-sync/1.0",
        }
        try:
            async with httpx.AsyncClient(headers=headers, timeout=60.0) as http_client:
                async with streamable_http_client(NOTION_MCP_URL, http_client=http_client) as (
                    read_stream,
                    write_stream,
                    _,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        tools_response = await session.list_tools()
                        tool_names = [t.name for t in tools_response.tools]

                        create_ds_name = _pick_create_data_source_tool(tool_names)
                        create_page_name = _pick_create_page_tool(tool_names)

                        if not create_ds_name:
                            print("Error: No create-data-source/database tool found.", file=sys.stderr)
                            print("Available tools:", tool_names, file=sys.stderr)
                            raise SyncAbort()

                        parent_page_id = NOTION_PARENT_PAGE_ID.replace("-", "")
                        create_ds_args = _create_ds_call_args(create_ds_name, parent_page_id)
                        result = await session.call_tool(create_ds_name, create_ds_args)
                        if result.isError:
                            print(f"Error creating data source: {result}", file=sys.stderr)
                            raise SyncAbort()

                        content = result.content
                        if not content:
                            print("Error: No content in create-data-source result.", file=sys.stderr)
                            raise SyncAbort()
                        text = content[0].text if hasattr(content[0], "text") else str(content[0])
                        data_source_id = None
                        try:
                            parsed = json.loads(text)
                            if isinstance(parsed, dict):
                                data_source_id = parsed.get("id") or parsed.get("data_source_id") or parsed.get("database_id")
                            elif isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                                data_source_id = parsed[0].get("id") or parsed[0].get("data_source_id")
                        except json.JSONDecodeError:
                            pass
                        if not data_source_id:
                            m = re.search(r'"id"\s*:\s*"([^"]+)"', text)
                            if m:
                                data_source_id = m.group(1)
                            if not data_source_id:
                                m2 = re.search(r"(collection://[^\s\"']+)", text)
                                if m2:
                                    data_source_id = m2.group(1)
                            if not data_source_id:
                                m3 = re.search(r"\b([a-f0-9]{32})\b", text, re.I)
                                if m3:
                                    data_source_id = m3.group(1)
                        if not data_source_id:
                            print("Could not parse data source ID from response. Result:", text[:500], file=sys.stderr)
                            raise SyncAbort()

                        ds_id = _normalize_data_source_id(str(data_source_id))
                        print(f"Database: {DB_TITLE} ({ds_id})")

                        if create_db_only:
                            print("Create-db-only: skipping pages.")
                            return

                        if not create_page_name:
                            print("Error: No create-page tool found. Available:", tool_names, file=sys.stderr)
                            raise SyncAbort()

                        created = 0
                        parent = {"type": "data_source_id", "data_source_id": ds_id}
                        batch_size = 100
                        for i in range(0, len(claims), batch_size):
                            chunk = claims[i : i + batch_size]
                            create_page_args = {
                                "parent": parent,
                                "pages": [{"properties": _claim_to_sqlite_properties(c)} for c in chunk],
                            }
                            page_result = await session.call_tool(create_page_name, create_page_args)
                            if page_result.isError:
                                print(f"Failed to create pages: {page_result}", file=sys.stderr)
                                continue
                            created += len(chunk)
                        print(f"Created {created} page(s) from {path.name}.")
                        return
        except SyncAbort:
            raise
        except Exception as e:
            last_error = e
            # Retry only for unauthorized cases.
            if "401" in str(e) and attempt < max_attempts - 1:
                refreshed = _refresh_access_token_if_possible()
                if refreshed:
                    print("Access token was unauthorized; refreshed token and retrying once...")
                    access_token = refreshed
                    continue
                if auto_auth:
                    print("Access token unauthorized; starting OAuth flow to get a new token...")
                    run_auth(port=auth_port, timeout_s=auth_timeout, no_browser=auth_no_browser)
                    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
                    access_token = current_access_token()
                    if access_token:
                        continue
            break

    # If we get here, all attempts failed.
    if last_error and "401" in str(last_error):
        print(
            "Error: Notion MCP returned 401 Unauthorized. Token is invalid/expired.\n"
            "Run `python get_notion_mcp_token.py` (or remove --no-auto-auth) and try again.",
            file=sys.stderr,
        )
        raise SyncAbort()
    if last_error:
        raise last_error


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync research claims JSON to Notion via Notion MCP (https://mcp.notion.com)"
    )
    parser.add_argument("json_file", type=Path, help="Path to research_claims_*.json")
    parser.add_argument(
        "--create-db-only",
        action="store_true",
        help="Only create/ensure database; do not add pages",
    )
    parser.add_argument(
        "--no-auto-auth",
        action="store_true",
        help="Disable automatic OAuth bootstrap if token is missing",
    )
    parser.add_argument(
        "--auth-port",
        type=int,
        default=8765,
        help="Local callback port for OAuth bootstrap (default: 8765)",
    )
    parser.add_argument(
        "--auth-timeout",
        type=int,
        default=180,
        help="OAuth callback timeout in seconds (default: 180)",
    )
    parser.add_argument(
        "--auth-no-browser",
        action="store_true",
        help="During auto-auth, print URL but do not auto-open browser",
    )
    args = parser.parse_args()

    if not args.json_file.is_file():
        print(f"Error: File not found: {args.json_file}", file=sys.stderr)
        sys.exit(1)

    def _contains_sync_abort(exc: BaseException) -> bool:
        if isinstance(exc, SyncAbort):
            return True
        nested = getattr(exc, "exceptions", None)
        if nested:
            return any(_contains_sync_abort(s) for s in nested)
        return False

    try:
        asyncio.run(
            _run_sync(
                args.json_file,
                args.create_db_only,
                auto_auth=not args.no_auto_auth,
                auth_port=args.auth_port,
                auth_timeout=args.auth_timeout,
                auth_no_browser=args.auth_no_browser,
            )
        )
    except SyncAbort:
        sys.exit(1)
    except BaseException as e:
        if _contains_sync_abort(e):
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()
