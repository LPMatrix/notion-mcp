#!/usr/bin/env python3
"""
Research pipeline: multi-step research → JSON output → sync to Notion via sync_to_notion.py.
Every assertion gets a provenance trail (source + confidence + contradiction).
Sync: run sync_to_notion.py <output.json> (uses Python MCP client → Notion hosted MCP).

Usage:
  python main.py "Your research topic"
  python main.py --fact-check-from path.json
  python main.py "Topic" --report
  python write_report.py research_claims_<slug>.json
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from config import OPENROUTER_API_KEY, TAVILY_API_KEY

from research import run_research
from fact_check import run_fact_check
from claims_store import write_claims_json, read_claims_json
from report import generate_report_markdown, report_output_path
from sync_to_notion import SyncAbort, publish_report_page


def slug(topic: str) -> str:
    """Safe filename slug from topic."""
    s = re.sub(r"[^\w\s-]", "", topic.lower())
    return re.sub(r"[-\s]+", "-", s).strip() or "research"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research pipeline: research topic → JSON → sync to Notion via MCP."
    )
    parser.add_argument("topic", nargs="?", help="Research topic to investigate")
    parser.add_argument(
        "--fact-check-from",
        metavar="JSON",
        help="Re-run fact-check on claims from this JSON file; writes research_claims_updated.json",
    )
    parser.add_argument(
        "--max-search",
        type=int,
        default=10,
        help="Max search results for research phase (default: 10)",
    )
    parser.add_argument(
        "--max-counter",
        type=int,
        default=5,
        help="Max counter-evidence results per claim (default: 5)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="After writing JSON, write research_report_<slug>.md and publish the same content to Notion "
        "(OPENROUTER_API_KEY + NOTION_MCP_ACCESS_TOKEN + NOTION_PARENT_PAGE_ID)",
    )
    parser.add_argument(
        "--report-title",
        default="",
        metavar="TITLE",
        help="Title for the Notion report page (default: Research report: <topic>)",
    )
    parser.add_argument(
        "--no-auto-auth",
        action="store_true",
        help="With --report, disable OAuth bootstrap if Notion token is missing",
    )
    parser.add_argument("--auth-port", type=int, default=8765)
    parser.add_argument("--auth-timeout", type=int, default=180)
    parser.add_argument("--auth-no-browser", action="store_true")
    args = parser.parse_args()

    def write_and_maybe_publish_report(topic_str: str, claims: list, json_path: Path) -> None:
        if not args.report:
            return
        if not OPENROUTER_API_KEY:
            print("Error: --report requires OPENROUTER_API_KEY in .env", file=sys.stderr)
            sys.exit(1)
        md = generate_report_markdown(topic_str, claims)
        rp = report_output_path(json_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(md, encoding="utf-8")
        print(f"Wrote report {rp}")
        title = (args.report_title or "").strip() or f"Research report: {topic_str or json_path.stem}"
        try:
            asyncio.run(
                publish_report_page(
                    md,
                    title,
                    auto_auth=not args.no_auto_auth,
                    auth_port=args.auth_port,
                    auth_timeout=args.auth_timeout,
                    auth_no_browser=args.auth_no_browser,
                )
            )
        except SyncAbort:
            sys.exit(1)

    if args.fact_check_from:
        in_path = Path(args.fact_check_from)
        if not in_path.is_file():
            print(f"Error: File not found: {in_path}", file=sys.stderr)
            sys.exit(1)
        topic, claims = read_claims_json(in_path)
        if not claims:
            print("No claims in file.", file=sys.stderr)
            sys.exit(1)
        print(f"Fact-checking {len(claims)} claim(s) from {in_path} ...")
        updated = run_fact_check(claims, max_counter_results=args.max_counter)
        out_path = Path("research_claims_updated.json")
        write_claims_json(updated, out_path, topic=topic)
        print(f"Wrote {len(updated)} claim(s) to {out_path}")
        write_and_maybe_publish_report(topic, updated, out_path)
        return

    if not args.topic or not args.topic.strip():
        print("Error: topic is required (or use --fact-check-from JSON).", file=sys.stderr)
        sys.exit(1)
    topic = args.topic.strip()

    if not TAVILY_API_KEY:
        print("Error: TAVILY_API_KEY is not set. Get a key at https://tavily.com and add it to .env", file=sys.stderr)
        sys.exit(1)
    if not OPENROUTER_API_KEY:
        print("Error: OPENROUTER_API_KEY is not set. Add it to .env", file=sys.stderr)
        sys.exit(1)

    out_path = Path(f"research_claims_{slug(topic)}.json")

    print(f"Running full pipeline for topic: {topic}")
    claims = run_research(topic, max_search_results=args.max_search)
    if not claims:
        print("No claims to fact-check.")
        write_claims_json(claims, out_path, topic=topic)
        print(f"Wrote {out_path}")
        return
    updated = run_fact_check(claims, max_counter_results=args.max_counter)
    write_claims_json(updated, out_path, topic=topic)
    print(f"Wrote {len(updated)} claim(s) to {out_path}")
    write_and_maybe_publish_report(topic, updated, out_path)
    print("\nTo sync to Notion: python sync_to_notion.py", out_path.name)


if __name__ == "__main__":
    main()
