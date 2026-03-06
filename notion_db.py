"""Notion client: create/use claims database, insert and update claim rows."""
from __future__ import annotations

from typing import Any

from notion_client import Client
from notion_client.typing import SyncAsync

from config import NOTION_DATABASE_ID, NOTION_PAGE_ID, NOTION_TOKEN

# Schema property names (must match Notion)
PROP_CLAIM = "Claim"
PROP_SOURCE_URL = "Source URL"
PROP_SOURCE_SNIPPET = "Source Snippet"
PROP_TOPIC = "Topic"
PROP_CONFIDENCE = "Confidence"
PROP_CONTRADICTION = "Contradiction"
PROP_FACT_CHECK_NOTES = "Fact-check notes"

CONFIDENCE_OPTIONS = ("High", "Medium", "Low", "Unverified")


def _rich_text(content: str, max_len: int = 2000) -> list[dict]:
    if not content:
        return []
    text = content[:max_len] + ("..." if len(content) > max_len else "")
    return [{"type": "text", "text": {"content": text}}]


def _title(content: str, max_len: int = 2000) -> list[dict]:
    if not content:
        return []
    text = content[:max_len] + ("..." if len(content) > max_len else "")
    return [{"type": "text", "text": {"content": text}}]


class NotionClaimsDB:
    def __init__(self, token: str | None = None, database_id: str | None = None, page_id: str | None = None):
        self._client = Client(auth=token or NOTION_TOKEN)
        self._database_id = database_id or NOTION_DATABASE_ID
        self._page_id = page_id or NOTION_PAGE_ID
        self._data_source_id: str | None = None

    def _get_data_source_id(self) -> str:
        """Resolve the first data source id for the database (required for query in API 2025-09-03)."""
        if self._data_source_id:
            return self._data_source_id
        db_id = self.get_database_id()
        db = self._client.databases.retrieve(database_id=db_id)
        sources = db.get("data_sources") or []
        if not sources:
            raise ValueError(
                f"Database {db_id} has no data sources. "
                "Ensure the database was created with initial_data_source (e.g. by this app)."
            )
        self._data_source_id = sources[0].get("id") or ""
        if not self._data_source_id:
            raise ValueError("Database data_sources[0] has no id.")
        return self._data_source_id

    def ensure_database(self) -> str:
        """Create a claims database under NOTION_PAGE_ID if we don't have NOTION_DATABASE_ID. Returns database_id."""
        if self._database_id:
            return self._database_id
        if not self._page_id:
            raise ValueError("Set NOTION_DATABASE_ID or both NOTION_PAGE_ID and NOTION_TOKEN to create a database")
        schema = {
            PROP_CLAIM: {"title": {}},
            PROP_SOURCE_URL: {"url": {}},
            PROP_SOURCE_SNIPPET: {"rich_text": {}},
            PROP_TOPIC: {"rich_text": {}},
            PROP_CONFIDENCE: {
                "select": {
                    "options": [{"name": o} for o in CONFIDENCE_OPTIONS],
                }
            },
            PROP_CONTRADICTION: {"checkbox": {}},
            PROP_FACT_CHECK_NOTES: {"rich_text": {}},
        }
        db = self._client.databases.create(
            parent={"type": "page_id", "page_id": self._page_id},
            title=[{"type": "text", "text": {"content": "Research claims"}}],
            initial_data_source={"properties": schema},
        )
        self._database_id = db["id"]
        return self._database_id

    def get_database_id(self) -> str:
        if not self._database_id:
            self._database_id = self.ensure_database()
        return self._database_id

    def insert_claim(
        self,
        claim: str,
        source_url: str,
        source_snippet: str,
        topic: str,
    ) -> dict:
        """Insert one claim row. Returns the created page (row)."""
        db_id = self.get_database_id()
        payload = {
            "parent": {"database_id": db_id},
            "properties": {
                PROP_CLAIM: {"title": _title(claim)},
                PROP_SOURCE_URL: {"url": source_url or None},
                PROP_SOURCE_SNIPPET: {"rich_text": _rich_text(source_snippet)},
                PROP_TOPIC: {"rich_text": _rich_text(topic)},
                PROP_CONFIDENCE: {"select": {"name": "Unverified"}},
                PROP_CONTRADICTION: {"checkbox": False},
                PROP_FACT_CHECK_NOTES: {"rich_text": []},
            },
        }
        return self._client.pages.create(**payload)

    def get_claims_for_topic(self, topic: str) -> list[dict]:
        """Query all claim rows matching the given topic (rich_text contains)."""
        data_source_id = self._get_data_source_id()
        results = []
        start_cursor = None
        while True:
            resp = self._client.data_sources.query(
                data_source_id,
                filter={
                    "property": PROP_TOPIC,
                    "rich_text": {"contains": topic},
                },
                start_cursor=start_cursor,
                page_size=100,
            )
            results.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            start_cursor = resp.get("next_cursor")
        return results

    def update_claim_fact_check(
        self,
        page_id: str,
        confidence: str,
        contradiction: bool,
        fact_check_notes: str,
    ) -> dict:
        """Update a claim row with fact-check results."""
        if confidence not in CONFIDENCE_OPTIONS:
            confidence = "Unverified"
        return self._client.pages.update(
            page_id=page_id,
            properties={
                PROP_CONFIDENCE: {"select": {"name": confidence}},
                PROP_CONTRADICTION: {"checkbox": contradiction},
                PROP_FACT_CHECK_NOTES: {"rich_text": _rich_text(fact_check_notes)},
            },
        )


def get_claim_text(page: dict) -> str:
    """Extract claim text from a Notion page (row)."""
    props = page.get("properties", {})
    title = props.get(PROP_CLAIM, {}).get("title", [])
    if not title:
        return ""
    return (title[0].get("plain_text") or "").strip()


def get_source_url(page: dict) -> str:
    props = page.get("properties", {})
    return (props.get(PROP_SOURCE_URL, {}).get("url") or "").strip()
