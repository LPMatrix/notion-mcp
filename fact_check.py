"""Adversarial fact-check phase: for each claim, search counter-evidence, assess. Returns enriched claim dicts."""
from __future__ import annotations

from search import search_counter_evidence
from llm import fact_check_claim
from claims_store import claim_row


def run_fact_check(claims: list[dict], max_counter_results: int = 5) -> list[dict]:
    """
    For each claim dict (claim, source_url, source_snippet, topic, ...):
    1. Search for counter-evidence
    2. Run adversarial fact-check via OpenRouter
    3. Return same list with confidence, contradiction, fact_check_notes set.
    """
    out = []
    for c in claims:
        claim = (c.get("claim") or "").strip()
        if not claim:
            out.append(c)
            continue
        source_url = (c.get("source_url") or "").strip()
        source_snippet = (c.get("source_snippet") or "").strip()
        topic = (c.get("topic") or "").strip()
        counter_results = search_counter_evidence(claim, max_results=max_counter_results)
        result = fact_check_claim(claim, source_url, source_snippet, counter_results)
        out.append(
            claim_row(
                claim=claim,
                source_url=source_url,
                source_snippet=source_snippet,
                topic=topic,
                confidence=result["confidence"],
                contradiction=result["contradiction"],
                fact_check_notes=result["fact_check_notes"],
                page_id=c.get("page_id"),
            )
        )
    return out
