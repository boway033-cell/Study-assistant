#!/bin/sh
set -eu

cd /code

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-9000}"
# Meoo image containers have ephemeral disks. This empty runtime directory is
# intentionally disposable until the Supabase persistence migration is done.
export DATA_DIR="${DATA_DIR:-/tmp/study-assistant-data}"

exec python -m uvicorn backend.app.main:app --host "$HOST" --port "$PORT"
