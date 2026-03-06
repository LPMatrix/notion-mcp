"""Adversarial fact-check phase: for each claim, search counter-evidence, assess, update Notion."""
from __future__ import annotations

from notion_db import NotionClaimsDB, get_claim_text, get_source_url
from search import search_counter_evidence
from llm import fact_check_claim


def run_fact_check(topic: str, db: NotionClaimsDB | None = None, max_counter_results: int = 5) -> list[dict]:
    """
    For each claim in Notion for this topic:
    1. Search for counter-evidence
    2. Run adversarial fact-check via OpenRouter
    3. Update the row with confidence, contradiction flag, and notes
    Returns the list of updated Notion pages.
    """
    if db is None:
        db = NotionClaimsDB()
    db.get_database_id()

    rows = db.get_claims_for_topic(topic)
    updated = []
    for page in rows:
        page_id = page["id"]
        claim = get_claim_text(page)
        source_url = get_source_url(page)
        props = page.get("properties", {})
        snippet_prop = props.get("Source Snippet", {}).get("rich_text", [])
        source_snippet = (snippet_prop[0].get("plain_text") or "").strip() if snippet_prop else ""

        if not claim:
            continue

        counter_results = search_counter_evidence(claim, max_results=max_counter_results)
        result = fact_check_claim(claim, source_url, source_snippet, counter_results)

        db.update_claim_fact_check(
            page_id=page_id,
            confidence=result["confidence"],
            contradiction=result["contradiction"],
            fact_check_notes=result["fact_check_notes"],
        )
        updated.append(page)
    return updated
