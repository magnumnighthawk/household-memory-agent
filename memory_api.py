from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from memory_agent import (
    MemoryItem,
    MemoryStore,
    RetrievalHit,
    SearchMode,
    answer_question,
)

# ----------------------------
# Structured Logging Setup
# ----------------------------
logging.basicConfig(
    level=os.getenv("MEMORY_LOG_LEVEL", "INFO"),
    format="%(message)s",
)
logger = logging.getLogger("memory_api")


def log_structured(data: dict[str, Any]) -> None:
    """Log structured JSON data."""
    logger.info(json.dumps(data))


# ----------------------------
# Request/Response Models
# ----------------------------
class AddItemRequest(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source_type: str = "manual"
    source_ref: str | None = None


class AddItemResponse(BaseModel):
    item_id: str
    title: str
    message: str


class SearchResponse(BaseModel):
    query: str
    hits: list[RetrievalHit]
    hit_count: int


class HealthResponse(BaseModel):
    status: str
    db_path: str
    db_accessible: bool


# ----------------------------
# Application Setup
# ----------------------------
def _default_db_path() -> str:
    return os.getenv("MEMORY_DB_PATH", os.path.join(os.getcwd(), "household_memory.sqlite3"))


DB_PATH = _default_db_path()
store = MemoryStore(DB_PATH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup."""
    await store.init()
    log_structured({
        "event": "startup",
        "db_path": DB_PATH,
        "timestamp": time.time(),
    })
    yield
    log_structured({
        "event": "shutdown",
        "timestamp": time.time(),
    })


app = FastAPI(
    title="Household Memory API",
    version="0.1.0",
    lifespan=lifespan,
)


# ----------------------------
# Middleware for Request Logging
# ----------------------------
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests with structured data."""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    
    start_time = time.time()
    
    response = await call_next(request)
    
    latency_ms = (time.time() - start_time) * 1000
    
    log_data = {
        "request_id": request_id,
        "route": request.url.path,
        "method": request.method,
        "latency_ms": round(latency_ms, 2),
        "status_code": response.status_code,
        "timestamp": time.time(),
    }
    
    # Add query params for search requests
    if request.url.query:
        log_data["query_params"] = str(request.url.query)
    
    log_structured(log_data)
    
    return response


# ----------------------------
# Endpoints
# ----------------------------
@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """
    Health check endpoint.
    Verifies DB connection and performs a simple SELECT query.
    """
    db_accessible = False
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT 1")
            result = await cursor.fetchone()
            db_accessible = result is not None and result[0] == 1
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        db_accessible = False
    
    status = "healthy" if db_accessible else "unhealthy"
    
    return HealthResponse(
        status=status,
        db_path=DB_PATH,
        db_accessible=db_accessible,
    )


@app.post("/items", response_model=AddItemResponse)
async def add_item(request: Request, item_request: AddItemRequest) -> AddItemResponse:
    """
    Add a new memory item.
    """
    try:
        # Create memory item
        item = MemoryItem(
            title=item_request.title,
            content=item_request.content,
            tags=item_request.tags,
            source_type=item_request.source_type,  # type: ignore
            source_ref=item_request.source_ref,
        )
        
        # Store in DB
        await store.add_item(item)
        
        # Log with structured data
        log_structured({
            "event": "item_added",
            "request_id": request.state.request_id,
            "item_id": item.id,
            "title": item.title,
            "source_type": item.source_type,
            "tags_count": len(item.tags),
            "timestamp": time.time(),
        })
        
        return AddItemResponse(
            item_id=item.id,
            title=item.title,
            message="Item stored successfully",
        )
    
    except Exception as e:
        logger.error(f"Error adding item: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to add item: {str(e)}")


@app.get("/search", response_model=SearchResponse)
async def search_items(
    request: Request,
    q: str = Query(..., description="Search query"),
    limit: int = Query(5, ge=1, le=20, description="Maximum number of results"),
    mode: SearchMode = Query("recall", description="Search mode: recall or precision"),
) -> SearchResponse:
    """
    Search memory items using FTS.
    """
    try:
        # Perform search
        hits = await store.search(q, limit=limit, mode=mode)
        
        # Calculate confidence if we have hits
        confidence = None
        if hits:
            top_rank = hits[0].rank
            if top_rank >= 0.55:
                confidence = "high"
            elif top_rank >= 0.35:
                confidence = "medium"
            else:
                confidence = "low"
        
        # Log with structured data
        log_structured({
            "event": "search_performed",
            "request_id": request.state.request_id,
            "query": q,
            "mode": mode,
            "hit_count": len(hits),
            "confidence": confidence,
            "top_rank": round(hits[0].rank, 3) if hits else None,
            "timestamp": time.time(),
        })
        
        return SearchResponse(
            query=q,
            hits=hits,
            hit_count=len(hits),
        )
    
    except Exception as e:
        logger.error(f"Error searching items: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


# ----------------------------
# Run with: uvicorn memory_api:app --host 0.0.0.0 --port 8088 --reload
# ----------------------------
