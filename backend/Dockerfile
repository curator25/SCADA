FROM python:3.13-slim

WORKDIR /app

# Install dependencies first so this layer is cached between code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code (config.json is bind-mounted at run time so the client
# can edit it without rebuilding the image)
COPY . .

# Unbuffered output so logs appear immediately in `docker compose logs`
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Health check: Docker marks the container "unhealthy" if the API stops
# responding (e.g. the process wedges), so a stuck backend is visible in
# `docker ps` instead of sitting there silently.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status==200 else 1)" || exit 1

CMD ["python", "main.py"]
