## Household memory agent - Architecture, Capability, MVP 1.0 & roadmap for future expansions

Source: ChatGPT
Date: 13-01-2026
Link: https://chatgpt.com/share/69663a1f-a488-8008-a9ad-365440a3d94a

Let’s build this like a self-hosted service: **a tiny “Household Memory” daemon + CLI** that ingests “household facts” and answers questions with **sources**, refusing when it can’t ground the answer.

The win condition: *anyone in the house can ask “when did we last…?” and get a confident answer or a clear “not in memory yet”.*

---

## 0) What we’re building (MVP)

### Core capabilities (v1)

1. **Ingest**: add notes/receipts/warranty info as plain text (copy/paste)
2. **Store**: SQLite with full-text search
3. **Ask**: query in natural language → retrieve relevant snippets → answer **with citations** (what note it came from)
4. **No hallucinations**: if retrieval is weak, answer “not found” and suggest what to add.

### What we are *not* doing yet (v2+)

* Email scraping, photo OCR, auto receipt parsing, embeddings, reminders

---

## 1) System architecture (agent-as-a-service)

### Components

* **Store**: SQLite

  * `items` table: raw notes/docs
  * `facts` table: extracted structured facts (optional v1, strong in v2)
  * `items_fts` FTS5 index for search
* **Ingestion pipeline**

  * normalize → (optional) extract candidate facts → persist
* **Query pipeline**

  * retrieve → decide if enough evidence → answer w/ citations

### “Agent” behavior that makes this trustworthy

* It **never answers from thin air**.
* It only uses retrieved snippets.
* It always returns citations and confidence.
* It says **“I don’t have that yet”** when memory is missing.

---

## 2) Data model (practical + extensible)

### Item (raw memory)

* `id`: UUID
* `title`: short label (“Boiler service invoice”)
* `source_type`: `manual | email | pdf | photo | web`
* `source_ref`: free text (file path, email msg id, etc.)
* `content`: raw text
* `tags`: `["warranty", "kitchen", "subscriptions"]`
* `created_at`

### Fact (structured memory, optional in v1)

* `predicate`: `"warranty_expires_on" | "purchase_date" | "serviced_on" | ...`
* `entity`: `"Boiler" | "Dyson V8" | "Fridge"`
* `value`: `"2027-04-01"`
* `evidence_item_id`: points back to item

In MVP you can skip fact extraction and just rely on FTS + “answer with citations”. Add `facts` once you want “when did we last service X” to be bulletproof.

---

## 3) MVP implementation (SQLite + FTS5 + grounded Q&A)

Below is a compact, production-shaped skeleton. It’s intentionally “boring service code” like self-hosting.

### Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install pydantic==2.* rich typer aiosqlite openai
```

> If you don’t want OpenAI yet, you can still do retrieval-only and just print top matches.

---

## 4) Code: `memory_agent.py` (single-file MVP)

```python
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

import aiosqlite
import typer
from pydantic import BaseModel, Field
from rich import print as rprint

# ----------------------------
# Models
# ----------------------------

SourceType = Literal["manual", "email", "pdf", "photo", "web"]

class MemoryItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    source_type: SourceType = "manual"
    source_ref: str | None = None
    content: str
    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class RetrievalHit(BaseModel):
    item_id: str
    title: str
    created_at: str
    snippet: str
    rank: float

class Answer(BaseModel):
    answer: str
    confidence: Literal["low", "medium", "high"]
    citations: list[RetrievalHit]
    follow_up_to_store: list[str] = Field(default_factory=list)

# ----------------------------
# Storage (SQLite + FTS5)
# ----------------------------

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_ref TEXT,
  content TEXT NOT NULL,
  tags_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- FTS index (content + title)
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts
USING fts5(
  id UNINDEXED,
  title,
  content,
  tokenize = "porter"
);

CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(id, title, content) VALUES (new.id, new.title, new.content);
END;

CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
  DELETE FROM items_fts WHERE id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
  DELETE FROM items_fts WHERE id = old.id;
  INSERT INTO items_fts(id, title, content) VALUES (new.id, new.title, new.content);
END;
"""

@dataclass(frozen=True)
class MemoryStore:
    db_path: str

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def add_item(self, item: MemoryItem) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO items (id, title, source_type, source_ref, content, tags_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.title,
                    item.source_type,
                    item.source_ref,
                    item.content,
                    json.dumps(item.tags),
                    item.created_at,
                ),
            )
            await db.commit()

    async def search(self, query: str, limit: int = 5) -> list[RetrievalHit]:
        # bm25() gives lower=better, so we invert into rank for convenience
        sql = """
        SELECT
          f.id,
          i.title,
          i.created_at,
          snippet(items_fts, 2, '[', ']', '…', 12) AS snip,
          bm25(items_fts) AS bm
        FROM items_fts f
        JOIN items i ON i.id = f.id
        WHERE items_fts MATCH ?
        ORDER BY bm ASC
        LIMIT ?;
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(sql, (query, limit))
        hits: list[RetrievalHit] = []
        for r in rows:
            bm = float(r["bm"])
            rank = 1.0 / (1.0 + max(bm, 0.0))
            hits.append(
                RetrievalHit(
                    item_id=r["id"],
                    title=r["title"],
                    created_at=r["created_at"],
                    snippet=r["snip"],
                    rank=rank,
                )
            )
        return hits

    async def get_item(self, item_id: str) -> MemoryItem | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await db.execute_fetchone("SELECT * FROM items WHERE id = ?", (item_id,))
        if not row:
            return None
        return MemoryItem(
            id=row["id"],
            title=row["title"],
            source_type=row["source_type"],
            source_ref=row["source_ref"],
            content=row["content"],
            tags=json.loads(row["tags_json"]),
            created_at=row["created_at"],
        )

# ----------------------------
# Grounded answering
# ----------------------------

def evidence_is_sufficient(hits: list[RetrievalHit]) -> bool:
    if not hits:
        return False
    # simple heuristic MVP: at least one decent hit OR multiple weak hits
    if hits[0].rank >= 0.35:
        return True
    if len(hits) >= 3 and sum(h.rank for h in hits[:3]) >= 0.75:
        return True
    return False

async def answer_question(store: MemoryStore, question: str) -> Answer:
    hits = await store.search(question, limit=5)
    if not evidence_is_sufficient(hits):
        return Answer(
            answer="I don’t have enough grounded information in Household Memory to answer that yet.",
            confidence="low",
            citations=hits,
            follow_up_to_store=[
                "Add a note/receipt/invoice related to this (with date, item/model, and outcome).",
                "If it’s a service event, store the provider name and the service date.",
            ],
        )

    # Retrieval-only MVP answer: summarise based on snippets (safe-ish).
    # v1.5: plug in an LLM but constrain it to only use retrieved content.
    bullet_citations = "\n".join(
        [f"- {h.title} ({h.created_at}): {h.snippet}" for h in hits[:3]]
    )
    return Answer(
        answer=(
            "Here’s what I found in Household Memory (grounded in stored notes):\n"
            f"{bullet_citations}\n\n"
            "If you want a single precise date/value answer, store the invoice/service note text explicitly."
        ),
        confidence="medium" if hits[0].rank < 0.55 else "high",
        citations=hits[:3],
    )

# ----------------------------
# CLI
# ----------------------------

app = typer.Typer(add_completion=False)

def _default_db_path() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, ".household_memory.sqlite3")

@app.command()
def init(db: str = typer.Option(_default_db_path(), help="Path to SQLite DB")) -> None:
    asyncio.run(MemoryStore(db).init())
    rprint(f"[green]Initialized[/green] {db}")

@app.command()
def add(
    title: str = typer.Option(...),
    content: str = typer.Option(..., help="Paste text content"),
    tags: str = typer.Option("", help="Comma-separated tags"),
    source_type: SourceType = typer.Option("manual"),
    source_ref: str = typer.Option("", help="Optional: filename/email id/url"),
    db: str = typer.Option(_default_db_path()),
) -> None:
    item = MemoryItem(
        title=title,
        content=content,
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        source_type=source_type,
        source_ref=source_ref or None,
    )
    async def _run() -> None:
        store = MemoryStore(db)
        await store.init()
        await store.add_item(item)
    asyncio.run(_run())
    rprint(f"[green]Stored[/green] {item.id} — {item.title}")

@app.command()
def ask(
    question: str,
    db: str = typer.Option(_default_db_path()),
) -> None:
    async def _run() -> Answer:
        store = MemoryStore(db)
        await store.init()
        return await answer_question(store, question)

    ans = asyncio.run(_run())
    rprint(f"\n[bold]Answer[/bold] ({ans.confidence}):\n{ans.answer}\n")
    if ans.citations:
        rprint("[bold]Citations[/bold]")
        for c in ans.citations:
            rprint(f"- {c.title} • {c.created_at} • rank={c.rank:.2f}\n  {c.snippet}")
    if ans.follow_up_to_store:
        rprint("\n[bold]To improve memory[/bold]")
        for f in ans.follow_up_to_store:
            rprint(f"- {f}")

if __name__ == "__main__":
    app()
```

---

## 5) Run it (your first “it’s alive” dopamine hit)

```bash
python memory_agent.py init

python memory_agent.py add \
  --title "Boiler service invoice" \
  --content "Boiler serviced on 2025-10-12 by ABC Heating. Invoice £120. Next service due 2026-10." \
  --tags "boiler,service,invoice"

python memory_agent.py ask "When was the boiler last serviced?"
```

You’ll get a grounded response with citations.

---

## 6) Make it household-friendly (so other people use it)

MVP UX patterns that work:

* **A single link** in the house: `http://memory.local` (v1.2)
* **Two buttons**: “Add memory” and “Ask”
* “Add memory” accepts:

  * a photo upload (later OCR)
  * paste text
  * forward email (later)

But don’t jump there yet. First prove value with CLI + your own usage for 1–2 weeks.

---

## 7) The critical upgrade that makes this “Immich-tier”: Structured Facts (v2)

Once you’re storing enough items, you’ll want:

* “Warranty expires on …”
* “Filter size is …”
* “Provider number is …”
* “Last serviced on …”

That’s when we add:

* a **FactExtractor agent** (LLM or regex-first)
* a **Verifier** that checks extracted facts against evidence text
* a **facts table** that powers precise answers

This is where agent-building becomes *fun*, because each upgrade is tangible.

---

## 8) Next step: pick your first 10 memories (seed data)

To make this useful fast, seed with 10 high-value memories:

1. boiler/annual service
2. internet provider + account number
3. house insurance renewal date
4. appliance warranties (washer/fridge)
5. car/bike service dates (if relevant)
6. smoke alarm battery replacement date
7. water filter / vacuum filter model
8. council tax / utility reference numbers
9. home network diagram (Deco/proxmox notes)
10. emergency contacts / key numbers

---

## What I’ll do next with you (no repo-browsing)

Tell me which direction you want for v1.1 and I’ll give you the code:

1. **Local web UI** (FastAPI + minimal HTML)
2. **WhatsApp/Telegram intake** (message → store)
3. **Email forwarding intake** (Gmail label → ingest)
4. **Fact extraction + verifier** (turn memory into queryable facts)

If you choose just one: I’d do **Fact extraction + verifier** next, because it turns “search” into “answers”.
