# Docker Deployment Guide

This guide covers deploying the Household Memory Agent using Docker and Docker Compose.

## Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+
- (Optional) OpenAI API key for LLM-powered features

## Quick Start

### 1. Setup Environment

Copy the example environment file and configure it:

```bash
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY (optional)
```

### 2. Build and Run

```bash
# Build and start the service
docker-compose up -d

# View logs
docker-compose logs -f memory-api

# Check health status
curl http://localhost:8088/health
```

### 3. Test the API

```bash
# Add a memory item
curl -X POST http://localhost:8088/items \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Memory",
    "content": "This is a test memory item",
    "tags": ["test"]
  }'

# Search for items
curl "http://localhost:8088/search?q=test&limit=5"
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MEMORY_DB_PATH` | Path to SQLite database file | `/data/household_memory.sqlite3` |
| `MEMORY_LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | `INFO` |
| `OPENAI_API_KEY` | OpenAI API key for LLM features | None (optional) |

### Port Binding

The default configuration binds to `127.0.0.1:8088` (LAN-only access):

```yaml
ports:
  - "127.0.0.1:8088:8088"  # LAN-only
```

For testing or if using a reverse proxy on the same host, you can change to:

```yaml
ports:
  - "8088:8088"  # Accessible from any interface
```

**Security Note:** If exposing on all interfaces, use a firewall or reverse proxy with authentication.

## Data Persistence

The SQLite database is stored in the `./data` directory, which is mounted as a volume:

```
./data/
  └── household_memory.sqlite3
```

This ensures data persists across container restarts and rebuilds.

## Health Checks

The service includes built-in health checks:

- **Endpoint:** `GET /health`
- **Interval:** 30 seconds
- **Timeout:** 5 seconds
- **Retries:** 3

Check container health:

```bash
docker-compose ps
# or
docker inspect memory-api --format='{{.State.Health.Status}}'
```

## Managing the Service

```bash
# Start the service
docker-compose up -d

# Stop the service
docker-compose down

# Restart the service
docker-compose restart

# View logs
docker-compose logs -f

# Rebuild after code changes
docker-compose up -d --build

# Remove everything including volumes (WARNING: deletes data)
docker-compose down -v
```

## Backup

### Manual Backup

```bash
# Copy the database file
cp ./data/household_memory.sqlite3 ./backups/household_memory-$(date +%Y%m%d-%H%M%S).sqlite3
```

### Automated Backup (Recommended)

Create a cron job on your host:

```bash
# Edit crontab
crontab -e

# Add daily backup at 2 AM
0 2 * * * cd /path/to/household_memory_agent && cp ./data/household_memory.sqlite3 ./backups/household_memory-$(date +\%Y\%m\%d).sqlite3
```

### SQLite Backup Command

For a consistent snapshot:

```bash
docker-compose exec memory-api sqlite3 /data/household_memory.sqlite3 ".backup /data/backup.sqlite3"
cp ./data/backup.sqlite3 ./backups/household_memory-$(date +%Y%m%d-%H%M%S).sqlite3
```

## Troubleshooting

### Container won't start

Check logs:
```bash
docker-compose logs memory-api
```

### Health check failing

Test the health endpoint manually:
```bash
docker-compose exec memory-api curl http://localhost:8088/health
```

### Database permission issues

Ensure the `data` directory is writable:
```bash
chmod -R 755 ./data
```

### Can't connect from host

If using `127.0.0.1:8088` binding, ensure you're accessing from the same machine. For remote access, use a reverse proxy or change the port binding.

## Next Steps

- **Step 3:** Set up Telegram or Slack bot integration
- **Step 4:** Configure reverse proxy (Nginx/Caddy) with basic auth
- **Step 5:** Set up automated backups
- **Step 6:** Add monitoring and alerting

## API Documentation

Once running, visit:
- Interactive API docs: http://localhost:8088/docs
- OpenAPI schema: http://localhost:8088/openapi.json

## Security Recommendations

1. **LAN-only deployment:** Keep the default `127.0.0.1` binding
2. **Reverse proxy:** Use Nginx/Caddy with basic auth for web access
3. **Firewall:** Restrict access to your home network subnet
4. **Backups:** Enable encryption for off-site backups
5. **Environment:** Never commit `.env` file to version control
