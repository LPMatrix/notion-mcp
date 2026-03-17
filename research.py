"""Research phase: search topic, extract claims via LLM. Returns list of claim dicts (no Notion)."""
from __future__ import annotations

import warnings

from search import search
from llm import extract_claims
from claims_store import claim_row


def run_research(topic: str, max_search_results: int = 10) -> list[dict]:
    """
    Run multi-step research on the topic:
    1. Search the web for the topic
    2. Extract discrete claims with sources via OpenRouter
    Returns list of claim dicts (claim, source_url, source_snippet, topic, confidence=Unverified, ...).
    Sync to Notion via Notion MCP (see README).
    """
    search_results = search(topic, max_results=max_search_results)
    if not search_results:
        warnings.warn(
            f"Search returned 0 results for topic {topic!r}. Check network or try a different query.",
            UserWarning,
        )
        return []

    claims = extract_claims(topic, search_results)
    if not claims:
        warnings.warn(
            f"LLM extracted 0 claims from {len(search_results)} search results for {topic!r}. "
            "Check OPENROUTER_MODEL and response format.",
            UserWarning,
        )
    out = []
    for c in claims:
        out.append(
            claim_row(
                claim=c["claim"],
                source_url=c["source_url"],
                source_snippet=c["source_snippet"],
                topic=topic,
            )
        )
    return out
