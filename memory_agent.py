from __future__ import annotations

import asyncio
import os
import json
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
SourceType = Literal["manual", "web", "pdf", "email", "photo"]

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
        # Sanitize query for FTS5 MATCH (remove special characters)
        import re
        sanitized_query = re.sub(r"[^\w\s]", " ", query)
        # bm25() gives lower=better, so we invert into rank for convenience
        sql = """
        SELECT
          f.id,
          i.title,
          i.created_at,
          snippet(items_fts, 2, '[bold yellow]', '[/bold yellow]', '…', 25) AS snip,
          bm25(items_fts) AS bm
        FROM items_fts f
        JOIN items i ON i.id = f.id
        WHERE items_fts MATCH ?
        ORDER BY bm ASC
        LIMIT ?;
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(sql, (sanitized_query, limit))
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
        sql = "SELECT * FROM items WHERE id = ?;"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            row = await db.execute_fetchone(sql, (item_id,))
        if row:
            return MemoryItem(
                id=row["id"],
                title=row["title"],
                source_type=row["source_type"],
                source_ref=row["source_ref"],
                content=row["content"],
                tags=json.loads(row["tags_json"]),
                created_at=row["created_at"],
            )
        return None
    
# ----------------------------
# Grounded answering
# ----------------------------
def evidence_is_sufficient(hits: list[RetrievalHit]) -> bool:
    if not hits:
        return False
    
    if hits[0].rank >= 0.35 or (len(hits) >= 3 and sum(hit.rank for hit in hits[:3]) > 0.75):
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
    cwd = os.getcwd()
    return os.path.join(cwd, "household_memory.sqlite3")

@app.command()
def init(db: str = typer.Option(_default_db_path(), help="Path to SQLite DB")):
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