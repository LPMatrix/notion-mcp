"""Synthesize a narrative research report from structured claims (Markdown)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from llm import get_client
from config import OPENROUTER_MODEL


def report_output_path(json_path: Path) -> Path:
    """Default `research_report_<slug>.md` next to `research_claims_<slug>.json`."""
    name = json_path.name
    if name.startswith("research_claims_") and name.endswith(".json"):
        slug_part = name[len("research_claims_") : -len(".json")]
        return json_path.parent / f"research_report_{slug_part}.md"
    return json_path.parent / f"research_report_{json_path.stem}.md"


def _claims_for_prompt(claims: list[dict[str, Any]], max_snippet: int = 400) -> str:
    lines: list[str] = []
    for i, c in enumerate(claims, start=1):
        claim = (c.get("claim") or "").strip()
        url = (c.get("source_url") or "").strip()
        snip = (c.get("source_snippet") or "").strip()
        if len(snip) > max_snippet:
            snip = snip[: max_snippet - 3] + "..."
        conf = (c.get("confidence") or "Unverified").strip()
        contra = c.get("contradiction", False)
        notes = (c.get("fact_check_notes") or "").strip()
        if len(notes) > 600:
            notes = notes[:597] + "..."
        lines.append(
            f"### Claim {i}\n"
            f"- **Assertion:** {claim}\n"
            f"- **Source:** {url}\n"
            f"- **Snippet:** {snip}\n"
            f"- **Confidence:** {conf} | **Contradiction flag:** {contra}\n"
            f"- **Fact-check notes:** {notes}\n"
        )
    return "\n".join(lines)


def generate_report_markdown(topic: str, claims: list[dict[str, Any]]) -> str:
    """
    Produce a Markdown report: executive summary, synthesis, limitations, open questions.
    Uses the same OpenRouter client as the rest of the pipeline.
    """
    client = get_client()
    body = _claims_for_prompt(claims)
    prompt = f"""You are writing a research report for an informed reader. The topic and structured claim-level evidence (with fact-check notes) are below.

Topic: {topic}

Evidence (one section per extracted claim):
{body}

Write a single Markdown document with these sections (use `##` headings exactly):
## Executive summary
## Synthesis
## Evidence map
## Limitations
## Open questions

Rules:
- Base conclusions only on the material above; do not invent sources or studies.
- In **Evidence map**, briefly note which claims share the same source URL vs independent sources.
- In **Limitations**, mention gaps in search scope, source types, and what was not verified.
- Use bullet lists where helpful. Keep the tone precise and readable.
- Do not wrap the output in a markdown code fence."""

    resp = client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.35,
    )
    text = (resp.choices[0].message.content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text
