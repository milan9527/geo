#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

PYTHON_BIN="${GEO_PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
      "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "Python 3.10 or newer is required." >&2
  exit 1
fi

if [ -x ".venv/bin/python" ] &&
  ! .venv/bin/python -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  mv .venv ".venv-python-backup-$(date -u +%Y%m%d%H%M%S)"
fi

if [ ! -x ".venv/bin/python" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

if ! .venv/bin/python -c "import psycopg, boto3, x402" >/dev/null 2>&1; then
  .venv/bin/pip install -r requirements.txt
fi

if [ "${AWS_DATA_API:-false}" != "true" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker is required to run local PostgreSQL." >&2
    exit 1
  fi

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
