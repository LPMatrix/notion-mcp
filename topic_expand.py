"""Expand a user topic into a structured brief and diverse search queries (LLM)."""
from __future__ import annotations

import json
import re
from typing import Any

from llm import get_client
from config import OPENROUTER_MODEL


def _strip_json_fenced(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```\w*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    return t


def expand_topic(topic: str) -> dict[str, Any]:
    """
    Produce a research brief: primary question, scope, subtopics, exclusions, and 3–6 search queries.
    On parse failure, returns a minimal fallback using the raw topic only.
    """
    topic = (topic or "").strip()
    if not topic:
        return _fallback_expansion("")

    client = get_client()
    prompt = f"""You are a research librarian. Given the user's research topic, produce a brief that will drive web search and claim extraction.

User topic: {topic}

Return a single JSON object only, no markdown. Use exactly these keys:
- "primary_question": string — one precise question the research should answer
- "scope": string — one sentence on what is in scope
- "subtopics": array of 2 to 5 short strings — distinct angles (e.g. mechanisms, outcomes, populations, timing)
- "exclude": string — what to avoid or treat skeptically (empty string if none)
- "search_queries": array of 3 to 6 strings — distinct Tavily search queries; use specific keywords and synonyms; no two queries identical

JSON only."""

    try:
        resp = client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.25,
        )
        text = _strip_json_fenced((resp.choices[0].message.content or "").strip())
        data = json.loads(text)
    except Exception:
        return _fallback_expansion(topic)

    if not isinstance(data, dict):
        return _fallback_expansion(topic)

    queries = data.get("search_queries")
    if not isinstance(queries, list) or not queries:
        return _fallback_expansion(topic)

    cleaned_queries: list[str] = []
    seen: set[str] = set()
    for q in queries:
        if not isinstance(q, str):
            continue
        s = q.strip()
        if len(s) < 3 or s.lower() in seen:
            continue
        seen.add(s.lower())
        cleaned_queries.append(s)
        if len(cleaned_queries) >= 6:
            break

    if len(cleaned_queries) < 1:
        return _fallback_expansion(topic)

    subtopics = data.get("subtopics")
    if not isinstance(subtopics, list):
        subtopics = []
    sub_clean = [str(x).strip() for x in subtopics if isinstance(x, str) and str(x).strip()][:5]

    return {
        "primary_question": str(data.get("primary_question") or topic).strip() or topic,
        "scope": str(data.get("scope") or "").strip(),
        "subtopics": sub_clean,
        "exclude": str(data.get("exclude") or "").strip(),
        "search_queries": cleaned_queries,
    }


def _fallback_expansion(topic: str) -> dict[str, Any]:
    return {
        "primary_question": topic,
        "scope": "",
        "subtopics": [],
        "exclude": "",
        "search_queries": [topic] if topic else ["research"],
    }
