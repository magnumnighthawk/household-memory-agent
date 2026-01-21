# Use Python slim image for smaller size
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies (curl for healthcheck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml ./
COPY memory_agent.py ./
COPY memory_api.py ./
COPY README.md ./

# Install the package with dependencies
RUN pip install -e .

# Create data directory
RUN mkdir -p /data

# Expose API port
EXPOSE 8088

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8088/health || exit 1

# Run the API server
CMD ["uvicorn", "memory_api:app", "--host", "0.0.0.0", "--port", "8088"]
