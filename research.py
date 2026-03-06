"""Research phase: search topic, extract claims via LLM, write to Notion."""
from __future__ import annotations

from notion_db import NotionClaimsDB
from search import search
from llm import extract_claims


def run_research(topic: str, db: NotionClaimsDB | None = None, max_search_results: int = 10) -> list[dict]:
    """
    Run multi-step research on the topic:
    1. Search the web for the topic
    2. Extract discrete claims with sources via OpenRouter
    3. Insert each claim into Notion
    Returns the list of created Notion pages (rows).
    """
    if db is None:
        db = NotionClaimsDB()
    db.get_database_id()

    search_results = search(topic, max_results=max_search_results)
    if not search_results:
        return []

    claims = extract_claims(topic, search_results)
    created = []
    for c in claims:
        row = db.insert_claim(
            claim=c["claim"],
            source_url=c["source_url"],
            source_snippet=c["source_snippet"],
            topic=topic,
        )
        created.append(row)
    return created
