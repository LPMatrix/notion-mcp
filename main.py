#!/usr/bin/env python3
"""
Research pipeline: multi-step research → Notion DB → adversarial fact-check.
Every assertion gets a provenance trail (source + confidence + contradiction flag).

Usage:
  python main.py "Your research topic"
  python main.py "Topic" --research-only
  python main.py "Topic" --fact-check-only
"""
from __future__ import annotations

import argparse
import sys

from config import NOTION_TOKEN, OPENROUTER_API_KEY

from notion_db import NotionClaimsDB
from research import run_research
from fact_check import run_fact_check


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research pipeline: research topic → Notion claims DB → adversarial fact-check."
    )
    parser.add_argument("topic", help="Research topic to investigate")
    parser.add_argument(
        "--research-only",
        action="store_true",
        help="Only run research phase (search + extract claims → Notion)",
    )
    parser.add_argument(
        "--fact-check-only",
        action="store_true",
        help="Only run fact-check phase on existing claims for this topic",
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
    topic = args.topic.strip()
    if not topic:
        print("Error: topic cannot be empty", file=sys.stderr)
        sys.exit(1)

    if not NOTION_TOKEN:
        print("Error: NOTION_TOKEN is not set. Copy .env.example to .env and add your Notion integration token.", file=sys.stderr)
        sys.exit(1)
    if not OPENROUTER_API_KEY and (not args.fact_check_only):
        print("Error: OPENROUTER_API_KEY is not set for research/fact-check. Add it to .env", file=sys.stderr)
        sys.exit(1)

    db = NotionClaimsDB()

    if args.fact_check_only:
        print(f"Fact-checking existing claims for topic: {topic}")
        updated = run_fact_check(topic, db=db, max_counter_results=args.max_counter)
        print(f"Updated {len(updated)} claim(s).")
        return

    if args.research_only:
        print(f"Running research only for topic: {topic}")
        created = run_research(topic, db=db, max_search_results=args.max_search)
        print(f"Inserted {len(created)} claim(s) into Notion.")
        return

    print(f"Running full pipeline for topic: {topic}")
    created = run_research(topic, db=db, max_search_results=args.max_search)
    print(f"Inserted {len(created)} claim(s).")
    if not created:
        print("No claims to fact-check.")
        return
    updated = run_fact_check(topic, db=db, max_counter_results=args.max_counter)
    print(f"Fact-checked {len(updated)} claim(s). Done.")


if __name__ == "__main__":
    main()
