"""Web search for research and fact-check (DuckDuckGo, no API key)."""
from __future__ import annotations

from typing import Any

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None


def search(query: str, max_results: int = 10) -> list[dict[str, str]]:
    """Return list of {title, href, body} from DuckDuckGo."""
    if DDGS is None:
        raise RuntimeError("Install duckduckgo-search: pip install duckduckgo-search")
    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title": (r.get("title") or "").strip(),
                "href": (r.get("href") or r.get("link") or "").strip(),
                "body": (r.get("body") or "").strip(),
            })
    return results


def search_counter_evidence(claim: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search for potential counter-evidence to a claim."""
    q = f'counterargument OR "evidence against" OR "contradicts" OR "dispute" {claim[:100]}'
    return search(q, max_results=max_results)
