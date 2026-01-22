"""
Telegram bot for Household Memory Agent.

Provides a chat interface for adding and searching household memories.
Uses long polling (no webhook/tunnel required for LAN deployment).
"""
from __future__ import annotations

import os
import re
import uuid
import logging
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ----------------------------
# Logging Setup
# ----------------------------
logging.basicConfig(
    level=os.getenv("BOT_LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ----------------------------
# Models (matching memory_api.py)
# ----------------------------
class AddItemRequest(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source_type: str = "manual"
    source_ref: str | None = None

class RetrievalHit(BaseModel):
    item_id: str
    title: str
    created_at: str
    snippet: str
    rank: float

# ----------------------------
# Configuration
# ----------------------------
@dataclass(frozen=True)
class Config:
    token: str
    api_base_url: str
    allow_users: set[int]  # empty => allow all (not recommended for production)

def load_config() -> Config:
    """Load configuration from environment variables."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
    
    api_base_url = os.environ.get(
        "MEMORY_API_BASE_URL", 
        "http://memory-api:8088"
    ).rstrip("/")
    
    raw_allow = os.environ.get("BOT_ALLOW_USERS", "").strip()
    allow_users = (
        {int(x) for x in raw_allow.split(",") if x.strip().isdigit()} 
        if raw_allow 
        else set()
    )
    
    return Config(
        token=token, 
        api_base_url=api_base_url, 
        allow_users=allow_users
    )

CFG = load_config()

# ----------------------------
# Helper Functions
# ----------------------------
def is_allowed_user(user_id: int | None) -> bool:
    """Check if user is allowed to use the bot."""
    if user_id is None:
        return False
    # If no allowlist is set, allow all users (log warning)
    if not CFG.allow_users:
        logger.warning(f"No allowlist configured. User {user_id} allowed by default.")
        return True
    return user_id in CFG.allow_users

def make_title_from_text(text: str) -> str:
    """Generate a title from text (truncate if needed)."""
    t = re.sub(r"\s+", " ", text.strip())
    if len(t) <= 60:
        return t
    return t[:57] + "..."

def format_hits(hits: list[RetrievalHit]) -> str:
    """Format search results for Telegram display."""
    if not hits:
        return "🔍 No matches found.\n\nTip: Try different keywords or add the information first."

    lines: list[str] = ["🔍 <b>Top matches:</b>\n"]
    for i, h in enumerate(hits[:3], start=1):
        lines.append(
            f"<b>{i}. {escape_html(h.title)}</b>\n"
            f"<i>📅 {escape_html(h.created_at[:10])} • rank {h.rank:.2f}</i>\n"
            f"{format_snippet_html(h.snippet)}\n"
        )
    return "\n".join(lines)

def escape_html(s: str) -> str:
    """Escape HTML special characters for Telegram."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
    )

def format_snippet_html(snippet: str) -> str:
    """Convert snippet with ** markers to HTML bold tags for Telegram."""
    # First escape HTML special characters
    escaped = escape_html(snippet)
    
    # Convert ** markers to <b> tags
    parts = escaped.split("**")
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:  # Odd indices are highlighted text
            result.append(f"<b>{part}</b>")
        else:
            result.append(part)
    
    return "".join(result)

# ----------------------------
# API Client Functions
# ----------------------------
async def api_add_item(req: AddItemRequest) -> str:
    """Add item to memory via API."""
    url = f"{CFG.api_base_url}/items"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url, json=req.model_dump())
            r.raise_for_status()
            data = r.json()
            return data.get("item_id", "stored")
    except httpx.HTTPError as e:
        logger.error(f"API add_item error: {e}")
        raise

async def api_search(q: str, limit: int = 3) -> list[RetrievalHit]:
    """Search memory via API."""
    url = f"{CFG.api_base_url}/search"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, params={"q": q, "limit": limit})
            r.raise_for_status()
            data = r.json()
            hits_raw = data.get("hits", [])
            return [RetrievalHit.model_validate(h) for h in hits_raw]
    except httpx.HTTPError as e:
        logger.error(f"API search error: {e}")
        raise

# ----------------------------
# Bot Command Handlers
# ----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    if not is_allowed_user(user_id):
        logger.warning(f"Unauthorized access attempt from user {user_id}")
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    msg = (
        "👋 <b>Household Memory is ready!</b>\n\n"
        "<b>Quick Guide:</b>\n"
        "• Simply send any message to <b>save</b> it as a memory\n"
        "• Start your message with <b>?</b> to <b>search</b>\n"
        "  Example: <code>? when was boiler serviced</code>\n\n"
        "<b>Commands:</b>\n"
        "• /add &lt;text&gt; — explicitly save a memory\n"
        "• /ask &lt;question&gt; — search for information\n"
        "• /about — learn what this bot does\n\n"
        "💡 <i>Tip: Just chat naturally. I'll remember everything.</i>"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /about command."""
    if not update.effective_user:
        return
    
    if not is_allowed_user(update.effective_user.id):
        return

    msg = (
        "🏠 <b>Household Memory Agent</b>\n\n"
        "I help you store and recall household information:\n"
        "• 📄 Receipts and invoices\n"
        "• 🔧 Service dates and warranties\n"
        "• 📞 Account numbers and references\n"
        "• 📝 General notes and reminders\n\n"
        "I search only what you've saved — if something's not here yet, just add it!\n\n"
        "🔒 <i>Privacy-first: All data stays local.</i>"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /add command."""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    if not is_allowed_user(user_id):
        return

    text = " ".join(context.args).strip() if context.args else ""
    if not text:
        await update.message.reply_text(
            "ℹ️ Usage: <code>/add &lt;text&gt;</code>\n\n"
            "Example: <code>/add Boiler serviced on 2025-10-12 by ABC Heating</code>",
            parse_mode=ParseMode.HTML
        )
        return

    title = make_title_from_text(text)
    req = AddItemRequest(
        title=title,
        content=text,
        source_type="manual",
        source_ref=f"telegram:{user_id}:{uuid.uuid4()}",
    )
    
    try:
        item_id = await api_add_item(req)
        logger.info(f"User {user_id} added item {item_id}")
        await update.message.reply_text(
            f"✅ <b>Saved</b>\n\n{escape_html(title)}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to add item for user {user_id}: {e}")
        await update.message.reply_text(
            "❌ Failed to save. Please try again later."
        )

async def ask_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ask command."""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    if not is_allowed_user(user_id):
        return

    q = " ".join(context.args).strip() if context.args else ""
    if not q:
        await update.message.reply_text(
            "ℹ️ Usage: <code>/ask &lt;question&gt;</code>\n\n"
            "Example: <code>/ask when was the boiler serviced</code>",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        hits = await api_search(q)
        logger.info(f"User {user_id} searched: {q} (found {len(hits)} hits)")
        await update.message.reply_text(format_hits(hits), parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Search failed for user {user_id}: {e}")
        await update.message.reply_text(
            "❌ Search failed. Please try again later."
        )

async def message_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle non-command messages: ? for search, anything else for add."""
    if not update.message or not update.message.text:
        return
    
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    if not is_allowed_user(user_id):
        return

    text = update.message.text.strip()

    # Question mode: starts with ?
    if text.startswith("?"):
        q = text.lstrip("?").strip()
        if not q:
            await update.message.reply_text(
                "ℹ️ Ask format: <code>? your question</code>\n\n"
                "Example: <code>? when was the boiler serviced</code>",
                parse_mode=ParseMode.HTML
            )
            return
        
        try:
            hits = await api_search(q)
            logger.info(f"User {user_id} searched: {q} (found {len(hits)} hits)")
            await update.message.reply_text(format_hits(hits), parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Search failed for user {user_id}: {e}")
            await update.message.reply_text(
                "❌ Search failed. Please try again later."
            )
        return

    # Default: treat as add
    title = make_title_from_text(text)
    req = AddItemRequest(
        title=title,
        content=text,
        source_type="manual",
        source_ref=f"telegram:{user_id}:{uuid.uuid4()}",
    )
    
    try:
        item_id = await api_add_item(req)
        logger.info(f"User {user_id} added item {item_id}")
        await update.message.reply_text(
            f"✅ <b>Saved</b>\n\n{escape_html(title)}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to add item for user {user_id}: {e}")
        await update.message.reply_text(
            "❌ Failed to save. Please try again later."
        )

# ----------------------------
# Main Application
# ----------------------------
def main() -> None:
    """Start the bot."""
    logger.info("Starting Household Memory Telegram Bot...")
    logger.info(f"API Base URL: {CFG.api_base_url}")
    logger.info(f"Allowed users: {CFG.allow_users if CFG.allow_users else 'ALL (⚠️ not recommended)'}")
    
    # Build application
    app = Application.builder().token(CFG.token).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("ask", ask_cmd))
    
    # Fallback for all non-command text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_fallback))

    # Start polling
    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
