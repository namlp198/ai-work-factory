FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY packages packages
COPY apps apps
COPY workers workers
RUN pip install --no-cache-dir ".[postgres,s3]" pytest

COPY config config
COPY agents agents
COPY workflows workflows
COPY knowledge knowledge
COPY infrastructure/migrations infrastructure/migrations
COPY examples examples

ENV AWF_HOME=/app PYTHONUNBUFFERED=1
RUN useradd --create-home awf && mkdir -p /app/var && chown -R awf /app/var
USER awf
EXPOSE 8000
CMD ["awf", "api", "--host", "0.0.0.0", "--port", "8000"]
