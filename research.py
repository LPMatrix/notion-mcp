"""Research phase: expand topic, search, extract claims via LLM. Returns claim dicts + expansion metadata."""
from __future__ import annotations

import warnings
from typing import Any
from urllib.parse import urlparse, urlunparse

from search import search
from llm import extract_claims
from claims_store import claim_row
from topic_expand import expand_topic


def _normalize_href(h: str) -> str:
    h = (h or "").strip()
    if not h:
        return ""
    if "://" not in h:
        h = "https://" + h
    try:
        p = urlparse(h)
        path = (p.path or "/").rstrip("/") or "/"
        netloc = (p.netloc or "").lower()
        scheme = (p.scheme or "https").lower()
        return urlunparse((scheme, netloc, path, "", "", ""))
    except Exception:
        return h.lower()


def _dedupe_results(results: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for r in results:
        key = _normalize_href(r.get("href", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _collect_search_results(queries: list[str], max_total: int) -> list[dict[str, str]]:
    """Run Tavily per query, dedupe by URL, return up to max_total results."""
    queries = [q.strip() for q in queries if q and str(q).strip()]
    if not queries:
        return []
    n = len(queries)
    per = max(2, min(10, (max_total + n - 1) // n))
    merged: list[dict[str, str]] = []
    for q in queries:
        merged.extend(search(q, max_results=per))
    deduped = _dedupe_results(merged)[:max_total]
    return deduped


def _minimal_expansion(topic: str) -> dict[str, Any]:
    t = (topic or "").strip() or "research"
    return {
        "primary_question": t,
        "scope": "",
        "subtopics": [],
        "exclude": "",
        "search_queries": [t],
    }


def run_research(
    topic: str,
    max_search_results: int = 10,
    *,
    use_topic_expansion: bool = True,
) -> tuple[list[dict], dict[str, Any]]:
    """
    Run multi-step research on the topic:
    1. Optionally expand the topic into search queries + brief (LLM)
    2. Search the web (merged, deduped URLs)
    3. Extract discrete claims with sources via OpenRouter

    Returns (list of claim dicts, topic_expansion dict for JSON / reports).
    """
    topic = (topic or "").strip()
    if use_topic_expansion:
        expansion = expand_topic(topic)
    else:
        expansion = _minimal_expansion(topic)

    queries = expansion.get("search_queries") or [topic]
    if not isinstance(queries, list):
        queries = [topic]

    search_results = _collect_search_results(queries, max_total=max_search_results)
    if not search_results:
        warnings.warn(
            f"Search returned 0 results for topic {topic!r} (queries: {queries}). "
            "Check network or try a different query.",
            UserWarning,
        )
        return [], expansion

    claims = extract_claims(topic, search_results, topic_expansion=expansion)
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
    return out, expansion
