from __future__ import annotations

import asyncio
import os
import json
import uuid
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from collections import defaultdict

import aiosqlite
import typer
from pydantic import BaseModel, Field
from rich import print as rprint
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# ----------------------------
# Models
# ----------------------------
SourceType = Literal["manual", "web", "pdf", "email", "photo"]
SearchMode = Literal["recall", "precision"]
_STOPWORDS = {
    "when", "was", "is", "are", "were", "the", "a", "an", "to", "for", "of",
    "in", "on", "at", "and", "or", "we", "i", "you", "our", "my", "last",
    "did", "do", "does", "done", "this", "that", "it", "from"
}

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

class QueryExpansion(BaseModel):
    queries: list[str] = Field(default_factory=list, description="Alternative FTS queries (short, keywordy)")
    keywords: list[str] = Field(default_factory=list, description="Important keywords/entities to include")

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

    async def search(self, query: str, limit: int = 5, mode: SearchMode = "recall") -> list[RetrievalHit]:
        fts_query = build_fts_query(query)

        if mode == "precision":
            # convert "a* OR b* OR c*" -> "a* AND b* AND c*"
            fts_query = fts_query.replace(" OR ", " AND ")

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
            cursor = await db.execute(sql, (fts_query, limit))
            rows = await cursor.fetchall()

        hits: list[RetrievalHit] = []
        for r in rows:
            bm = float(r["bm"])

            # Robust to positive or negative bm:
            # lower magnitude => better, so use abs
            rank = 1.0 / (1.0 + abs(bm))

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
            cursor = await db.execute(sql, (item_id,))
            row = await cursor.fetchone()
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
def build_fts_query(raw: str) -> str:
    """
    Build an FTS5 MATCH query string that is:
    - robust to punctuation
    - biased toward recall (OR)
    - improves matching via prefix search (token*)
    """
    # Keep alphanumerics, convert others to space
    cleaned = re.sub(r"[^\w\s]", " ", raw.lower()).strip()
    tokens = [t for t in cleaned.split() if t]

    # Remove stopwords but keep numbers/model-ish tokens
    kept: list[str] = []
    for t in tokens:
        if t in _STOPWORDS:
            continue
        kept.append(t)

    # If everything got removed, fall back to original tokens
    if not kept:
        kept = tokens

    # Prefix-expand "word" tokens (but not short ones or pure numbers)
    def token_to_fts(t: str) -> str:
        if t.isdigit():
            return t
        if len(t) <= 2:
            return t
        # FTS5 prefix search: token*
        return f"{t}*"

    fts_terms = [token_to_fts(t) for t in kept]

    # OR query for recall
    return " OR ".join(fts_terms)

def evidence_is_sufficient(hits: list[RetrievalHit]) -> bool:
    if not hits:
        return False
    
    if hits[0].rank >= 0.35 or (len(hits) >= 3 and sum(hit.rank for hit in hits[:3]) > 0.75):
        return True
    
    return False

def merge_hits(hit_lists: list[list[RetrievalHit]], top_k: int = 5) -> list[RetrievalHit]:
    best: dict[str, RetrievalHit] = {}
    counts: defaultdict[str, int] = defaultdict(int)

    for hits in hit_lists:
        for h in hits:
            counts[h.item_id] += 1
            prev = best.get(h.item_id)
            if prev is None or h.rank > prev.rank:
                best[h.item_id] = h

    merged = list(best.values())

    # consensus bonus (small, bounded)
    for h in merged:
        c = counts[h.item_id]
        bonus = min(0.10, 0.03 * (c - 1))
        h.rank = min(0.999, h.rank + bonus)

    merged.sort(key=lambda x: x.rank, reverse=True)
    return merged[:top_k]

async def expand_query_llm(question: str) -> QueryExpansion:
    # If no key, degrade gracefully to “no expansion”
    if not os.getenv("OPENAI_API_KEY"):
        return QueryExpansion(queries=[], keywords=[])

    client = OpenAI()

    prompt = (
        "You generate short search queries for a household memory database.\n"
        "Return alternative keyword-style queries that could match stored notes.\n"
        "Use synonyms and abbreviations; include likely entity words.\n"
        "Keep each query short (2–6 tokens). Prefer stems like 'servic*' when useful.\n"
        f"Question: {question}"
    )

    resp = client.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a search query expansion assistant."},
            {"role": "user", "content": prompt}
        ],
        response_format=QueryExpansion,
        temperature=0.3,
    )

    # Parse the response using the built-in Pydantic parser
    return resp.choices[0].message.parsed

async def retrieve(store: MemoryStore, question: str, limit: int = 5) -> list[RetrievalHit]:
    base_hits = await store.search(question, limit=limit)
    rprint(f"[cyan]DEBUG base_hits:[/cyan] {base_hits}")
    
    expansion = await expand_query_llm(question)
    rprint(f"[cyan]DEBUG expansion:[/cyan] {expansion}")
    # Keep it bounded
    expanded_queries = (expansion.queries or [])[:6]

    hit_lists: list[list[RetrievalHit]] = [base_hits]
    for q in expanded_queries:
        hit_lists.append(await store.search(q, limit=limit))

    return merge_hits(hit_lists, top_k=limit)

async def answer_question(store: MemoryStore, question: str) -> Answer:
    hits = await retrieve(store, question, limit=5)

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

    bullet_citations = "\n".join(
        [f"- {h.title} ({h.created_at}): {h.snippet}" for h in hits[:3]]
    )
    return Answer(
        answer=(
            "Here’s what I found in Household Memory (grounded in stored notes):\n"
            f"{bullet_citations}\n"
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