#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -f ".env.aws" ]; then
  echo ".env.aws is missing. Provision AWS resources first." >&2
  exit 1
fi

set -a
source .env.aws
if [ -f ".env.deploy.aws" ]; then
  source .env.deploy.aws
fi
set +a

exec ./scripts/start.sh
