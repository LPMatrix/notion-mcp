"""In-memory claim model and JSON file I/O for MCP sync. No Notion REST."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Schema property names for Notion MCP (SQL DDL columns + notion-create-pages SQLite values)
PROP_CLAIM = "Claim"
PROP_SOURCE_URL = "Source URL"
PROP_SOURCE_SNIPPET = "Source Snippet"
PROP_TOPIC = "Topic"
PROP_CONFIDENCE = "Confidence"
PROP_CONTRADICTION = "Contradiction"
PROP_FACT_CHECK_NOTES = "Fact-check notes"

CONFIDENCE_OPTIONS = ("High", "Medium", "Low", "Unverified")


def claim_row(
    claim: str,
    source_url: str,
    source_snippet: str,
    topic: str,
    confidence: str = "Unverified",
    contradiction: bool = False,
    fact_check_notes: str = "",
    page_id: str | None = None,
) -> dict[str, Any]:
    """One claim as a dict for JSON or for Notion MCP properties."""
    return {
        "claim": claim.strip(),
        "source_url": (source_url or "").strip(),
        "source_snippet": (source_snippet or "").strip(),
        "topic": topic.strip(),
        "confidence": confidence if confidence in CONFIDENCE_OPTIONS else "Unverified",
        "contradiction": bool(contradiction),
        "fact_check_notes": (fact_check_notes or "").strip(),
        "page_id": page_id,
    }


def write_claims_json(claims: list[dict], path: str | Path, topic: str = "") -> Path:
    """Write claims (with optional topic) to a JSON file. Returns path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"topic": topic, "count": len(claims), "claims": claims}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


def read_claims_json(path: str | Path) -> tuple[str, list[dict]]:
    """Read topic and claims from JSON. Returns (topic, claims)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("topic", ""), data.get("claims", [])
