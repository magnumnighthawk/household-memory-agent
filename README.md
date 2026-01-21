# Household Memory Agent

A privacy-focused tool for storing and retrieving household-related notes, receipts, invoices, and service records. Uses a local SQLite database with FTS5 full-text search and optional LLM-powered query expansion for intelligent retrieval.

**Access via:**
- **Telegram Bot** — Natural chat interface (recommended for household adoption)
- **REST API** — HTTP endpoints for integrations
- **CLI** — Command-line interface for power users

## Features

- **Local-first storage**: All data stays on your machine in SQLite
- **Full-text search**: Powered by SQLite FTS5 with Porter stemming
- **LLM-enhanced retrieval**: Optional OpenAI integration for query expansion and better search results
- **Grounded answers**: Only answers when evidence is sufficient, with confidence scoring
- **Source citations**: Every answer includes references to the source memories
- **Multiple source types**: Support for manual entries, web, PDF, email, and photo sources
- **Tagging system**: Organize memories with custom tags
- **Telegram Bot**: Chat-based interface for easy household adoption

## Quick Start

### Option 1: Telegram Bot (Recommended for Households)

1. **Create a Telegram bot** via @BotFather (get your token)

2. **Configure environment:**
   ```sh
   cp .env.example .env
   # Edit .env and add your TELEGRAM_BOT_TOKEN
   ```

3. **Start with Docker Compose:**
   ```sh
   docker compose up -d --build
   ```

4. **Get your Telegram user ID:**
   ```sh
   docker compose logs -f memory-bot
   # Message your bot /start, check logs for your user ID
   ```

5. **Secure the bot** by adding your user ID to `.env`:
   ```env
   BOT_ALLOW_USERS=12345678,98765432
   ```

6. **Restart:**
   ```sh
   docker compose restart memory-bot
   ```

**Usage:**
- Send any message to save it as a memory
- Start messages with `?` to search (e.g., `? when was boiler serviced`)

See [Telegram Bot Setup Guide](idea/012-telegram-bot-setup.md) for details.

### Option 2: CLI

1. **Install dependencies:**
   ```sh
   pip install -e .
   ```

2. **Initialize the database:**
   ```sh
   python memory_agent.py init
   ```

3. **Add a memory item:**
   ```sh
   python memory_agent.py add \
     --title "Boiler Annual Service" \
     --content "Serviced by ABC Heating on 2025-12-01. Model: Worcester Bosch 30i. Next service due Dec 2026." \
     --tags "boiler,service,maintenance,2025" \
     --source-type "manual"
   ```

4. **Ask a question:**
   ```sh
   python memory_agent.py ask "When was the boiler last serviced?"
   ```

## Commands

### `init`
Initialize the database schema. Safe to run multiple times.

```sh
python memory_agent.py init [--db PATH]
```

### `add`
Add a new memory item to the database.

```sh
python memory_agent.py add \
  --title "Memory Title" \
  --content "Detailed content with dates, models, providers, etc." \
  --tags "tag1,tag2,tag3" \
  --source-type [manual|web|pdf|email|photo] \
  --source-ref "optional-reference" \
  --db PATH
```

**Parameters:**
- `--title`: Short descriptive title (required)
- `--content`: Full text content (required)
- `--tags`: Comma-separated tags (optional)
- `--source-type`: Type of source (default: manual)
- `--source-ref`: Reference to source file/URL/email ID (optional)
- `--db`: Database path (default: ./household_memory.sqlite3)

### `ask`
Query the memory database with natural language questions.

```sh
python memory_agent.py ask "your question here" [--db PATH]
```

The system will:
1. Expand your query using LLM (if OpenAI API key is configured)
2. Search the database using FTS5
3. Merge and rank results
4. Return an answer with confidence level and citations
5. Suggest what to store if evidence is insufficient

## Configuration

### Optional: OpenAI API Key

For enhanced query expansion, set your OpenAI API key:

```sh
export OPENAI_API_KEY="sk-..."
```

Or create a `.env` file:
```
OPENAI_API_KEY=sk-...
```

**Without an API key**, the system will still work using direct FTS5 search without query expansion.

## Requirements

- **For Docker (Telegram Bot + API):**
  - Docker and Docker Compose
  - Telegram bot token from @BotFather
  - Optional: OpenAI API key

- **For CLI:**
  - Python 3.10 or higher
  - Dependencies (install via `pip install -e .`):
    - aiosqlite >= 0.19.0
    - typer >= 0.9.0
    - pydantic >= 2.0.0
    - rich >= 13.0.0
    - openai >= 1.0.0
    - python-dotenv >= 1.0.0
    - python-telegram-bot >= 21.0 (for bot)
    - httpx >= 0.27.0 (for bot)

## How It Works

### Storage Layer
- Uses SQLite with WAL mode for concurrent access
- FTS5 virtual table with Porter stemming for fuzzy matching
- Automatic triggers keep FTS index synchronized

### Retrieval Pipeline
1. **Query processing**: Strips punctuation, removes stopwords, applies prefix matching
2. **Query expansion** (optional): LLM generates alternative queries and keywords
3. **Search**: Runs multiple FTS queries in parallel
4. **Merging**: Combines results with consensus-based boosting
5. **Ranking**: BM25 scoring with normalization

### Answer Generation
- Evaluates if retrieved evidence is sufficient (rank thresholds)
- Only answers when confidence >= medium
- Always includes citations with snippets
- Suggests improvements when evidence is lacking

## Examples

```sh
# Add a warranty record
python memory_agent.py add \
  --title "Dyson V11 Warranty" \
  --content "Purchased from Amazon on 2024-03-15. 2-year warranty expires 2026-03-15. Serial: AB123456789" \
  --tags "warranty,vacuum,dyson"

# Add a subscription
python memory_agent.py add \
  --title "Netflix Subscription" \
  --content "Monthly subscription started 2023-01-01. £10.99/month. Next renewal: 2026-02-01" \
  --tags "subscription,streaming,monthly"

# Query examples
python memory_agent.py ask "when does the dyson warranty expire?"
python memory_agent.py ask "what subscriptions do we have?"
python memory_agent.py ask "broadband contract end"
```

## Architecture

This is a **grounded** memory agent:
- **No hallucinations**: Refuses to answer without evidence
- **Traceable**: Every answer cites source documents
- **Local-first**: Privacy-focused, self-hosted design
- **Extensible**: Easy to add new source types or fact extraction

## Privacy & Data

- All data is stored locally in SQLite
- No data is sent to external services except:
  - Query expansion requests to OpenAI API (optional, query text only)
  - Telegram bot API (only for bot updates, no memory data sent)
- Database file: `household_memory.sqlite3` (default location)
- WAL files: `household_memory.sqlite3-wal` and `-shm` (temporary)

## API Reference

The REST API is available at `http://localhost:8088` (when using Docker).

### Endpoints

- `GET /health` — Health check
- `POST /items` — Add a memory item
- `GET /search?q=<query>&limit=5` — Search memories

See [DEPLOYMENT.md](DEPLOYMENT.md) for API details.

## Components

- **[telegram_bot.py](telegram_bot.py)** — Telegram bot interface
- **[memory_api.py](memory_api.py)** — REST API server
- **[memory_agent.py](memory_agent.py)** — Core logic and CLI
- **[docker-compose.yml](docker-compose.yml)** — Service orchestration

## Development

Install with dev dependencies:
```sh
pip install -e ".[dev]"
```

Run tests:
```sh
pytest
```

## Future Enhancements

- [x] Telegram bot interface
- [x] REST API
- [x] Docker deployment
- [ ] Photo/attachment support in Telegram
- [ ] Email scraping and auto-ingestion
- [ ] PDF/receipt OCR
- [ ] Photo/image analysis
- [ ] Structured fact extraction
- [ ] Reminder system based on dates
- [ ] Web UI
- [ ] Multi-user support

## License

See LICENSE file for details.
