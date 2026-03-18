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
from pathlib import Path

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

# Load .env from project root
load_dotenv(Path(__file__).resolve().parent / ".env")

NOTION_MCP_URL = "https://mcp.notion.com/mcp"
NOTION_MCP_ACCESS_TOKEN = os.environ.get("NOTION_MCP_ACCESS_TOKEN", "").strip()
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


def _rich_text(content: str) -> list[dict]:
    """Build Notion rich_text array; single segment."""
    if not (content or "").strip():
        return []
    return [{"type": "text", "text": {"content": content.strip()}}]


def _claim_to_properties(c: dict) -> dict:
    """Map one claim dict to Notion page properties for MCP tool args."""
    confidence = (c.get("confidence") or "Unverified").strip()
    if confidence not in CONFIDENCE_OPTIONS:
        confidence = "Unverified"
    return {
        PROP_CLAIM: {"title": [{"type": "text", "text": {"content": (c.get("claim") or "")[:2000]}}]},
        PROP_SOURCE_URL: {"url": (c.get("source_url") or "").strip() or ""},
        PROP_SOURCE_SNIPPET: {"rich_text": _rich_text(c.get("source_snippet") or "")},
        PROP_TOPIC: {"rich_text": _rich_text(c.get("topic") or "")},
        PROP_CONFIDENCE: {"select": {"name": confidence}},
        PROP_CONTRADICTION: {"checkbox": bool(c.get("contradiction"))},
        PROP_FACT_CHECK_NOTES: {"rich_text": _rich_text(c.get("fact_check_notes") or "")},
    }


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


async def _run_sync(path: Path, create_db_only: bool) -> None:
    """Connect to Notion MCP, create data source and pages."""
    if not NOTION_MCP_ACCESS_TOKEN:
        print(
            "Error: Set NOTION_MCP_ACCESS_TOKEN in .env (OAuth access token for Notion MCP).",
            file=sys.stderr,
        )
        sys.exit(1)
    if not NOTION_PARENT_PAGE_ID:
        print(
            "Error: Set NOTION_PARENT_PAGE_ID in .env (page ID where the database will live).",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    claims = data.get("claims", [])
    if not claims:
        print("No claims in file. Exiting.")
        return

    headers = {
        "Authorization": f"Bearer {NOTION_MCP_ACCESS_TOKEN}",
        "User-Agent": "notion-mcp-sync/1.0",
    }
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

                create_ds_name = None
                create_page_name = None
                for name in tool_names:
                    n = name.lower().replace("-", "_").replace(" ", "_")
                    if "data_source" in n or ("create" in n and "database" in n):
                        create_ds_name = name
                    if "create" in n and "page" in n:
                        create_page_name = name
                if not create_ds_name:
                    create_ds_name = "create-a-data-source" if "create-a-data-source" in tool_names else (tool_names[0] if tool_names else None)
                if not create_page_name:
                    create_page_name = "notion-create-pages" if "notion-create-pages" in tool_names else "create-page" if "create-page" in tool_names else None

                if not create_ds_name:
                    print("Error: No create-data-source/database tool found.", file=sys.stderr)
                    print("Available tools:", tool_names, file=sys.stderr)
                    sys.exit(1)

                parent_page_id = NOTION_PARENT_PAGE_ID.replace("-", "")
                create_ds_args = {
                    "parent": {"type": "page_id", "page_id": parent_page_id},
                    "title": [{"type": "text", "text": {"content": DB_TITLE}}],
                    "properties": _database_schema(),
                }
                result = await session.call_tool(create_ds_name, create_ds_args)
                if result.is_error:
                    print(f"Error creating data source: {result}", file=sys.stderr)
                    sys.exit(1)

                content = result.content
                if not content:
                    print("Error: No content in create-data-source result.", file=sys.stderr)
                    sys.exit(1)
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
                    sys.exit(1)

                print(f"Database: {DB_TITLE} ({data_source_id})")

                if create_db_only:
                    print("Create-db-only: skipping pages.")
                    return

                if not create_page_name:
                    print("Error: No create-page tool found. Available:", tool_names, file=sys.stderr)
                    sys.exit(1)

                created = 0
                for c in claims:
                    props = _claim_to_properties(c)
                    create_page_args = {
                        "parent": {"database_id": data_source_id},
                        "properties": props,
                    }
                    page_result = None
                    try:
                        page_result = await session.call_tool(create_page_name, create_page_args)
                    except Exception as e1:
                        try:
                            create_page_args["parent"] = {"data_source_id": data_source_id}
                            page_result = await session.call_tool(create_page_name, create_page_args)
                        except Exception as e2:
                            print(f"Failed to create page for claim: {e1}; {e2}", file=sys.stderr)
                            continue
                    if page_result.is_error:
                        print(f"Failed to create page: {page_result}", file=sys.stderr)
                        continue
                    created += 1
                print(f"Created {created} page(s) from {path.name}.")


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
    args = parser.parse_args()

    if not args.json_file.is_file():
        print(f"Error: File not found: {args.json_file}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_run_sync(args.json_file, args.create_db_only))


if __name__ == "__main__":
    main()
