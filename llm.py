"""OpenRouter LLM client for claim extraction and adversarial fact-check."""
from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_MODEL

_openrouter_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _openrouter_client
    if _openrouter_client is None:
        _openrouter_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
        )
    return _openrouter_client


def set_client(client: OpenAI) -> None:
    global _openrouter_client
    _openrouter_client = client


def extract_claims(
    topic: str,
    search_results: list[dict[str, str]],
    *,
    topic_expansion: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """
    Given a research topic and search results, return a list of
    {claim, source_url, source_snippet} with one discrete assertion per item.
    """
    client = get_client()
    results_text = "\n\n".join(
        f"[{i+1}] Title: {r.get('title', '')}\nURL: {r.get('href', '')}\nSnippet: {r.get('body', '')[:800]}"
        for i, r in enumerate(search_results)
    )
    brief_block = ""
    if topic_expansion:
        pq = (topic_expansion.get("primary_question") or topic).strip()
        sc = (topic_expansion.get("scope") or "").strip()
        subs = topic_expansion.get("subtopics") or []
        excl = (topic_expansion.get("exclude") or "").strip()
        sub_lines = "\n".join(f"- {s}" for s in subs if isinstance(s, str) and s.strip())
        brief_block = f"""
Research brief (use this to focus claims; every claim must still cite a source below):
- Primary question: {pq}
- Scope: {sc or "(not specified)"}
- Angles to cover:
{sub_lines or "- (see primary question)"}
- Avoid or treat skeptically: {excl or "(none specified)"}
"""
    prompt = f"""You are a research analyst. Given the topic and the search results below, extract discrete, factual claims that are supported by a specific source.

Topic: {topic}
{brief_block}
Search results:
{results_text}

For each claim:
- State one clear, verifiable assertion (no vague summaries).
- Assign it to the exact source (URL) that supports it.
- Provide a short source snippet (1-2 sentences) from that source.

Respond with a JSON array only, no markdown or explanation. Each element must have keys: "claim", "source_url", "source_snippet".
Example: [{{"claim": "...", "source_url": "https://...", "source_snippet": "..."}}]"""

    resp = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    text = (resp.choices[0].message.content or "").strip()
    # Strip markdown code block if present
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = []
    if not isinstance(data, list):
        data = []
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("claim"):
            out.append({
                "claim": str(item["claim"]).strip(),
                "source_url": str(item.get("source_url") or "").strip(),
                "source_snippet": str(item.get("source_snippet") or "").strip(),
            })
    return out


def fact_check_claim(
    claim: str,
    source_url: str,
    source_snippet: str,
    counter_search_results: list[dict[str, str]],
) -> dict[str, Any]:
    """
    Adversarially assess the claim given original source and counter-evidence search.
    Returns dict with: confidence (High/Medium/Low/Unverified), contradiction (bool), fact_check_notes (str).
    """
    client = get_client()
    counter_text = "\n\n".join(
        f"- [{r.get('href', '')}] {r.get('title', '')}: {r.get('body', '')[:500]}"
        for r in counter_search_results
    )
    prompt = f"""You are an adversarial fact-checker. Your job is to challenge the following claim and rate its reliability.

Claim: {claim}
Original source URL: {source_url}
Original source snippet: {source_snippet}

Potential counter-evidence or related search results:
{counter_text or "(No counter-evidence found)"}

Tasks:
1. Rate confidence in the claim: High (well-supported, consistent with evidence), Medium (plausible but limited or mixed evidence), Low (weak or contested), Unverified (cannot verify or no reliable evidence).
2. Decide if there is a clear contradiction (another reliable source contradicts the claim). Answer true or false.
3. Write brief fact-check notes (2-4 sentences): what supports or undermines the claim, and any caveats.

Respond with a single JSON object only, no markdown. Use exactly these keys: "confidence", "contradiction", "fact_check_notes".
- "confidence" must be one of: High, Medium, Low, Unverified
- "contradiction" must be true or false
- "fact_check_notes" is a string"""

    resp = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    text = (resp.choices[0].message.content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {}
    confidence = (data.get("confidence") or "Unverified").strip()
    if confidence not in ("High", "Medium", "Low", "Unverified"):
        confidence = "Unverified"
    contradiction = bool(data.get("contradiction", False))
    fact_check_notes = str(data.get("fact_check_notes") or "").strip()
    return {
        "confidence": confidence,
        "contradiction": contradiction,
        "fact_check_notes": fact_check_notes,
    }
