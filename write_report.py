#!/usr/bin/env python3
"""
Generate a Markdown research report from research_claims_*.json and publish the same
content as a Notion page (child under NOTION_PARENT_PAGE_ID).

Requires OPENROUTER_API_KEY and the same Notion MCP env as sync_to_notion.py.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from claims_store import read_claims_json
from config import OPENROUTER_API_KEY
from report import generate_report_markdown, report_output_path
from sync_to_notion import SyncAbort, publish_report_page


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write research_report_<slug>.md and publish the same content to Notion."
    )
    parser.add_argument("json_file", type=Path, help="Path to research_claims_*.json")
    parser.add_argument(
        "--title",
        default="",
        help="Notion page title (default: Research report: <topic>)",
    )
    parser.add_argument(
        "--no-auto-auth",
        action="store_true",
        help="Disable OAuth bootstrap if Notion token is missing",
    )
    parser.add_argument(
        "--auth-port",
        type=int,
        default=8765,
    )
    parser.add_argument(
        "--auth-timeout",
        type=int,
        default=180,
    )
    parser.add_argument(
        "--auth-no-browser",
        action="store_true",
    )
    args = parser.parse_args()

    if not args.json_file.is_file():
        print(f"Error: File not found: {args.json_file}", file=sys.stderr)
        sys.exit(1)

    if not OPENROUTER_API_KEY:
        print("Error: OPENROUTER_API_KEY is not set. Add it to .env", file=sys.stderr)
        sys.exit(1)

    topic, claims = read_claims_json(args.json_file)
    if not claims:
        print("No claims in file.", file=sys.stderr)
        sys.exit(1)

    print(f"Generating report for {len(claims)} claim(s) ...")
    md = generate_report_markdown(topic, claims)
    out = report_output_path(args.json_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"Wrote {out}")

    page_title = (args.title or "").strip() or f"Research report: {topic or args.json_file.stem}"
    try:
        asyncio.run(
            publish_report_page(
                md,
                page_title,
                auto_auth=not args.no_auto_auth,
                auth_port=args.auth_port,
                auth_timeout=args.auth_timeout,
                auth_no_browser=args.auth_no_browser,
            )
        )
    except SyncAbort:
        sys.exit(1)


if __name__ == "__main__":
    main()
