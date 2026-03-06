# Research pipeline (anti-hallucination)

Multi-step research agent that writes **claims + sources** into a Notion database, then runs an **adversarial fact-check** pass to set confidence and flag contradictions. Every assertion has a provenance trail.

- **Stack:** Python, OpenRouter (LLM), Notion API, Tavily (search)
- **No frontend:** Notion is the UI; run via CLI.

## Setup

1. **Notion**
   - Create an [integration](https://www.notion.so/my-integrations) and copy the token.
   - Create a page that will hold the database (or use an existing page). Share that page (or the DB) with the integration.
   - Copy the page ID from the URL (`.../page_id`) or the database ID if you already have a DB.

2. **OpenRouter**
   - Get an API key at [OpenRouter](https://openrouter.ai/keys).

3. **Tavily**
   - Get an API key at [Tavily](https://tavily.com) for web search.

4. **Env**
   ```bash
   cp .env.example .env
   # Edit .env: NOTION_TOKEN, NOTION_PAGE_ID (or NOTION_DATABASE_ID), OPENROUTER_API_KEY, TAVILY_API_KEY
   ```

5. **Install**
   ```bash
   pip install -r requirements.txt
   ```

## Usage

```bash
# Full pipeline: research → Notion → fact-check
python main.py "Effects of caffeine on sleep quality"

# Research only (no fact-check)
python main.py "Your topic" --research-only

# Fact-check only (re-run on existing claims for that topic)
python main.py "Your topic" --fact-check-only

# Tune search size
python main.py "Topic" --max-search 15 --max-counter 5
```

## Notion database schema

If you don’t set `NOTION_DATABASE_ID`, the script creates a database under `NOTION_PAGE_ID` with:

| Property         | Type      | Description                    |
|------------------|-----------|--------------------------------|
| Claim            | Title     | The assertion                  |
| Source URL       | URL       | Link to source                 |
| Source Snippet   | Rich text | Quote/snippet from source      |
| Topic            | Rich text | Research topic / run label     |
| Confidence       | Select    | High / Medium / Low / Unverified |
| Contradiction    | Checkbox  | Flagged as contradicted        |
| Fact-check notes | Rich text | Adversarial assessment        |

## Flow

1. **Research:** Search the web for the topic → LLM extracts discrete claims with source URL + snippet → each claim is inserted as a row.
2. **Fact-check:** For each claim, search for counter-evidence → LLM scores confidence and contradiction → row is updated with Confidence, Contradiction, and Fact-check notes.

The “living document” is the Notion database (and any views/pages you build on top of it).

