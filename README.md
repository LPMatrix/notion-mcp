# Research pipeline (anti-hallucination)

Multi-step research agent that produces **claims + sources + fact-check** as JSON. You sync to Notion using **[Notion MCP](https://developers.notion.com/docs/mcp)** (no REST API or integration token).

- **Stack:** Python, OpenRouter (LLM), Tavily (search), **Notion MCP** for sync
- **No frontend:** CLI writes JSON; Notion is updated via MCP in Cursor (or another MCP client).

## Setup

1. **OpenRouter** — Get an API key at [OpenRouter](https://openrouter.ai/keys).
2. **Tavily** — Get an API key at [Tavily](https://tavily.com) for web search.
3. **Notion MCP** — In Cursor: Settings → MCP → add server with `"url": "https://mcp.notion.com/mcp"`. Complete OAuth when you first use a Notion tool. See [Connecting to Notion MCP](https://developers.notion.com/docs/get-started-with-mcp).
4. **Env**
   ```bash
   cp .env.example .env
   # Edit .env: OPENROUTER_API_KEY, TAVILY_API_KEY
   ```
5. **Install**
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

**Option A: Notion MCP in Cursor**  
With Notion MCP connected in Cursor, ask the agent: *"Create a Notion database called 'Research claims' with columns: Claim (title), Source URL (url), Source Snippet (rich text), Topic (rich text), Confidence (select: High, Medium, Low, Unverified), Contradiction (checkbox), Fact-check notes (rich text). Then create one page per claim from the file `research_claims_<slug>.json`."*

**Option B: Python sync script (Notion MCP)**  
The `sync_to_notion.py` script uses the **Python MCP SDK** and connects to Notion’s hosted MCP at `https://mcp.notion.com/mcp` (Streamable HTTP). No Node or REST API required.

1. Get an **OAuth access token** for Notion MCP (e.g. connect once in Cursor via Settings → MCP → Notion and complete OAuth; for script use you need a token from your own OAuth flow or a one-time login helper — see [Notion: Integrating your own MCP client](https://developers.notion.com/guides/mcp/build-mcp-client)).
2. In Notion, create or pick a page that will contain the database and copy its page ID from the URL.
3. In `.env` set:
   - `NOTION_MCP_ACCESS_TOKEN` — OAuth access token for Notion MCP
   - `NOTION_PARENT_PAGE_ID` — page ID where the “Research claims” database will be created
4. Run:
   ```bash
   python sync_to_notion.py research_claims_effects-of-caffeine-on-sleep-quality.json
   ```
   This creates a database named **Research claims** on that page and one page per claim.

### 3. Optional: fact-check existing Notion data

- Export or fetch the claim pages from Notion (e.g. via MCP **notion-query-data-sources** or **notion-fetch**), save to a JSON file in the same shape (`topic` + `claims` with `claim`, `source_url`, `source_snippet`, `topic`).
- Run: `python main.py --fact-check-from that_file.json -o updated.json`
- Use MCP **notion-update-page** to update each page’s Confidence, Contradiction, and Fact-check notes from `updated.json`.

## Notion database schema (for MCP)

When creating the database with Notion MCP, use these properties so they match the JSON:

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
4. **Sync:** You (or the agent) use Notion MCP in Cursor to create/update the database and pages from the JSON.

The “living document” is the Notion database once synced; every assertion has a provenance trail via the properties above.
