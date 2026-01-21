# Telegram Bot - Testing Checklist

Quick verification steps after implementing the Telegram bot.

## Pre-flight Checks

- [ ] Telegram bot created via @BotFather
- [ ] Bot token added to `.env` file
- [ ] Docker and Docker Compose installed

## Build & Start

```bash
# Build and start services
docker compose up --build

# Or in detached mode
docker compose up -d --build
```

## Verify Services

```bash
# Check both services are running
docker compose ps

# Should show:
# memory-api   running
# memory-bot   running

# Check API health
curl http://localhost:8088/health

# Should return:
# {"status":"healthy","db_path":"/data/household_memory.sqlite3","db_accessible":true}
```

## Bot Testing

### 1. Get Your User ID

```bash
# Watch bot logs
docker compose logs -f memory-bot
```

In Telegram, message your bot: `/start`

Logs should show:
```
WARNING - No allowlist configured. User 12345678 allowed by default.
```

Copy your user ID.

### 2. Configure Allowlist

Stop services:
```bash
docker compose down
```

Add your user ID to `.env`:
```env
BOT_ALLOW_USERS=12345678
```

Restart:
```bash
docker compose up -d
```

### 3. Test Bot Commands

In Telegram, test these:

**Welcome:**
```
/start
```
✅ Should show welcome message with usage guide

**About:**
```
/about
```
✅ Should explain what the bot does

**Add memory (explicit):**
```
/add Boiler serviced on 2025-10-12 by ABC Heating
```
✅ Should confirm: "✅ Saved"

**Add memory (natural):**
```
Paid electricity bill £120 on 2025-01-15
```
✅ Should confirm: "✅ Saved"

**Search (explicit):**
```
/ask when was the boiler serviced
```
✅ Should return search results with boiler service info

**Search (natural):**
```
? electricity bill
```
✅ Should return search results with bill info

**No results:**
```
? car insurance
```
✅ Should show "No matches found" with helpful tip

## Verify Data Persistence

```bash
# Check database file exists
ls -lh data/household_memory.sqlite3

# View database records
docker compose exec memory-api sqlite3 /data/household_memory.sqlite3 "SELECT title, created_at FROM items LIMIT 5;"
```

## Security Verification

### Unauthorized Access

Get a friend's Telegram user ID (or create a second test account).

Message the bot from unauthorized account:
```
/start
```

✅ Bot should respond: "⛔ You are not authorized to use this bot."

✅ Logs should show: "WARNING - Unauthorized access attempt from user ..."

## Performance Check

```bash
# Monitor resource usage
docker stats memory-api memory-bot

# Check response times in logs
docker compose logs memory-bot | grep "latency_ms"
```

Typical values:
- Memory: < 100MB per service
- CPU: < 5% idle, spikes to 20-30% during search
- Response time: < 500ms for search, < 200ms for add

## Common Issues

### Bot not responding

1. Check logs:
   ```bash
   docker compose logs memory-bot
   ```

2. Verify token is correct in `.env`

3. Check API is healthy:
   ```bash
   curl http://localhost:8088/health
   ```

### "Connection refused" errors

API not ready yet. Wait for health check to pass:
```bash
docker compose logs memory-api | grep healthy
```

### User not authorized

Check `BOT_ALLOW_USERS` in `.env` matches your Telegram user ID.

Restart bot after changing:
```bash
docker compose restart memory-bot
```

## Cleanup

```bash
# Stop services
docker compose down

# Stop and remove volumes (⚠️ deletes data)
docker compose down -v

# Remove images
docker compose down --rmi all
```

## Next Steps

Once everything works:

- [ ] Add family members to `BOT_ALLOW_USERS`
- [ ] Test with real household data
- [ ] Review search quality
- [ ] Consider setting up reverse proxy for remote access
- [ ] Plan backup strategy for database

## Success Criteria

✅ Bot responds to commands
✅ Data is saved and searchable
✅ Unauthorized users are blocked
✅ Services restart automatically
✅ Data persists across restarts
✅ Logs are clean (no errors)
