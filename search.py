"""Web search for research and fact-check (DuckDuckGo via ddgs, no API key)."""
from __future__ import annotations

import warnings

try:
    from ddgs import DDGS
except ImportError:
    DDGS = None


def search(query: str, max_results: int = 10) -> list[dict[str, str]]:
    """Return list of {title, href, body} from web search."""
    if DDGS is None:
        raise RuntimeError("Install ddgs: pip install ddgs")
    results = []
    try:
        ddgs = DDGS()
        raw = ddgs.text(query, max_results=max_results)
        # Consume generator/list to avoid unclosed connections
        items = list(raw) if raw is not None else []
        for r in items:
            if not isinstance(r, dict):
                continue
            results.append({
                "title": (r.get("title") or r.get("name") or "").strip(),
                "href": (r.get("href") or r.get("link") or r.get("url") or "").strip(),
                "body": (r.get("body") or r.get("snippet") or r.get("description") or "").strip(),
            })
    except Exception as e:
        warnings.warn(f"Search failed for query {query!r}: {e}", UserWarning)
    return results


def search_counter_evidence(claim: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search for potential counter-evidence to a claim."""
    q = f'counterargument OR "evidence against" OR "contradicts" OR "dispute" {claim[:100]}'
    return search(q, max_results=max_results)
