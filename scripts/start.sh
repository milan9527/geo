#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required to run PostgreSQL." >&2
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi

if ! .venv/bin/python -c "import psycopg, boto3" >/dev/null 2>&1; then
  .venv/bin/pip install -r requirements.txt
fi

if [ "${AWS_DATA_API:-false}" != "true" ]; then
  docker compose up -d postgres

  for attempt in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U geo -d geo >/dev/null 2>&1; then
      break
    fi
    if [ "$attempt" -eq 30 ]; then
      echo "PostgreSQL did not become healthy in time." >&2
      exit 1
    fi
    sleep 1
  done

  export DATABASE_URL="${DATABASE_URL:-postgresql://geo:geo_dev_password@127.0.0.1:55432/geo}"
fi
exec .venv/bin/python server.py
