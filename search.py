"""Web search for research and fact-check via Tavily API."""
from __future__ import annotations

import warnings

import httpx

from config import TAVILY_API_KEY


def _normalize_result(r: dict) -> dict[str, str]:
    """Map Tavily result to {title, href, body}."""
    return {
        "title": (r.get("title") or "").strip(),
        "href": (r.get("href") or r.get("url") or "").strip(),
        "body": (r.get("body") or r.get("content") or "").strip(),
    }


def search(query: str, max_results: int = 10, *, _api_key: str | None = None) -> list[dict[str, str]]:
    """Return list of {title, href, body} from Tavily search."""
    api_key = _api_key or TAVILY_API_KEY
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is required. Set it in .env (get a key at https://tavily.com).")
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "max_results": min(max_results, 20),
    }
    results = []
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post("https://api.tavily.com/search", json=payload)
            resp.raise_for_status()
            data = resp.json()
        for r in data.get("results") or []:
            if isinstance(r, dict):
                results.append(_normalize_result({
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "content": r.get("content"),
                }))
    except Exception as e:
        warnings.warn(f"Tavily search failed for query {query!r}: {e}", UserWarning)
    return results


def search_counter_evidence(claim: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search for potential counter-evidence to a claim."""
    q = f'counterargument OR "evidence against" OR "contradicts" OR "dispute" {claim[:100]}'
    return search(q, max_results=max_results)
