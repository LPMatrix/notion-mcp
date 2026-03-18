#!/usr/bin/env python3
"""
Research pipeline: multi-step research → JSON output → sync to Notion via sync_to_notion.py.
Every assertion gets a provenance trail (source + confidence + contradiction).
Sync: run sync_to_notion.py <output.json> (uses Python MCP client → Notion hosted MCP).

Usage:
  python main.py "Your research topic" [--output path.json]
  python main.py "Topic" --research-only [--output path.json]
  python main.py --fact-check-from path.json [--output path.json]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from config import OPENROUTER_API_KEY, TAVILY_API_KEY

from research import run_research
from fact_check import run_fact_check
from claims_store import write_claims_json, read_claims_json


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
        "--research-only",
        action="store_true",
        help="Only run research phase (no fact-check); write claims with Unverified confidence.",
    )
    parser.add_argument(
        "--fact-check-from",
        metavar="JSON",
        help="Re-run fact-check on claims from this JSON file; write updated JSON to --output.",
    )
    parser.add_argument(
        "--output",
        "-o",
        metavar="PATH",
        default=None,
        help="Output JSON path (default: research_claims_<topic_slug>.json or stdout if --fact-check-from).",
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
    args = parser.parse_args()

    if args.fact_check_from:
        if not args.output:
            args.output = "research_claims_updated.json"
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
        out_path = Path(args.output) if args.output else None
        if out_path:
            write_claims_json(updated, out_path, topic=topic)
            print(f"Wrote {len(updated)} claim(s) to {out_path}")
        else:
            import json
            print(json.dumps({"topic": topic, "claims": updated}, indent=2))
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

    default_output = f"research_claims_{slug(topic)}.json"
    out_path = Path(args.output) if args.output else Path(default_output)

    if args.research_only:
        print(f"Running research only for topic: {topic}")
        claims = run_research(topic, max_search_results=args.max_search)
        write_claims_json(claims, out_path, topic=topic)
        print(f"Wrote {len(claims)} claim(s) to {out_path}")
        print("\nTo sync to Notion: python sync_to_notion.py", out_path.name)
        return

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
    print("\nTo sync to Notion: python sync_to_notion.py", out_path.name)


if __name__ == "__main__":
    main()
