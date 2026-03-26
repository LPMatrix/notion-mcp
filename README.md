# Research pipeline (anti-hallucination)

Multi-step research agent that produces **claims + sources + fact-check** as JSON. Sync to Notion via the included Python script, which uses the **Python MCP SDK** and Notion’s hosted MCP (`https://mcp.notion.com/mcp`).

- **Stack:** Python, OpenRouter (LLM), Tavily (search), **Notion MCP** for sync (Python client → hosted MCP)
- **No frontend:** CLI writes JSON; `sync_to_notion.py` pushes to Notion.

**Notion MCP Challenge** — This project was built for the [DEV Notion MCP Challenge](https://dev.to/challenges/notion-2026-03-04) (March 2026). Notion MCP is used as the sync layer: `sync_to_notion.py` acts as an MCP **client** (Python SDK, Streamable HTTP) talking to Notion’s hosted MCP at `https://mcp.notion.com/mcp` to create a “Research claims” database and one page per claim from the pipeline’s JSON output.

**Requirements:** Python 3.10+ (for the MCP SDK).

## Setup

1. **OpenRouter** — Get an API key at [OpenRouter](https://openrouter.ai/keys).
2. **Tavily** — Get an API key at [Tavily](https://tavily.com) for web search.
3. **Env**
   ```bash
   cp .env.example .env
   # Edit .env: OPENROUTER_API_KEY, TAVILY_API_KEY (required for pipeline).
   # For sync: NOTION_MCP_ACCESS_TOKEN, NOTION_PARENT_PAGE_ID (see Sync to Notion below).
   ```
4. **Install**
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### 1. Run the pipeline (writes JSON)

```bash
# Full pipeline: research → fact-check → write JSON
python main.py "Effects of caffeine on sleep quality"

# Custom output path
python main.py "Your topic" -o my_claims.json

# Research only (no fact-check; confidence stays Unverified)
python main.py "Your topic" --research-only

# Re-run fact-check on an existing JSON file
python main.py --fact-check-from research_claims_my-topic.json -o updated.json
```

Output is written to `research_claims_<topic_slug>.json` (or `-o` path). Each claim has: `claim`, `source_url`, `source_snippet`, `topic`, `confidence`, `contradiction`, `fact_check_notes`.

### 2. Sync to Notion

The `sync_to_notion.py` script uses the **Python MCP SDK** and connects to Notion’s hosted MCP at `https://mcp.notion.com/mcp` (Streamable HTTP). No Node or REST API required.

1. In Notion, create or pick a page that will contain the database and copy its page ID from the URL.
2. Set `NOTION_PARENT_PAGE_ID` in `.env`.
3. Run sync directly (single-command flow):
   ```bash
   python sync_to_notion.py research_claims_effects-of-caffeine-on-sleep-quality.json
   ```
   If token is missing, the script auto-starts OAuth bootstrap, opens browser consent, and writes to `.env`:
   - `NOTION_MCP_ACCESS_TOKEN`
   - `NOTION_MCP_REFRESH_TOKEN`
   - `NOTION_MCP_CLIENT_ID`
4. (Optional) Bootstrap token separately:
   ```bash
   python get_notion_mcp_token.py
   ```
5. The sync creates a database named **Research claims** on that page and one page per claim.

Notes:
- You can refresh tokens non-interactively with:
  ```bash
  python get_notion_mcp_token.py --refresh-only
  ```
- `sync_to_notion.py` will also attempt refresh automatically if access token is missing but refresh credentials exist.
- To disable auto OAuth bootstrap in sync: `python sync_to_notion.py ... --no-auto-auth`

### 3. Optional: fact-check existing Notion data

- Export or fetch the claim pages from Notion into a JSON file in the same shape (`topic` + `claims` with `claim`, `source_url`, `source_snippet`, `topic`).
- Run: `python main.py --fact-check-from that_file.json -o updated.json`
- Re-run `sync_to_notion.py` with the updated JSON to create a new database with the revised claims, or update pages in Notion by other means.

## Notion database schema

The sync script creates a database with these properties (they match the JSON):

| Property         | Type      | Description                    |
|------------------|-----------|--------------------------------|
| Claim            | Title     | The assertion                 |
| Source URL       | URL       | Link to source                 |
| Source Snippet   | Rich text | Quote/snippet from source      |
| Topic            | Rich text | Research topic / run label     |
| Confidence       | Select    | High / Medium / Low / Unverified |
| Contradiction    | Checkbox  | Flagged as contradicted        |
| Fact-check notes | Rich text | Adversarial assessment         |

## Flow

1. **Research:** Search (Tavily) → LLM extracts claims with source URL + snippet → list of claim dicts.
2. **Fact-check:** For each claim, search counter-evidence → LLM sets confidence, contradiction, notes → enriched claim dicts.
3. **Output:** JSON file with `topic` and `claims`.
4. **Sync:** Run `sync_to_notion.py` to create the database and pages in Notion from the JSON (Python MCP client → Notion hosted MCP).

The “living document” is the Notion database once synced; every assertion has a provenance trail via the properties above.
