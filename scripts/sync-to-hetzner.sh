#!/bin/bash
set -euo pipefail

HOST="${1:-hetzner}"
REMOTE_DIR="/home/nikolay/projects/agmem"

echo "Syncing to ${HOST}:${REMOTE_DIR} ..."

# Copy non-dot stuff: src/, tests/, scripts/, pyproject.toml, uv.lock, README.md, LICENSE, DESIGN.md, CLAUDE.md
rsync -avz --progress \
  --exclude '.*' \
  --exclude '.agmem/' \
  --exclude '.venv/' \
  --exclude '.pytest_cache/' \
  /Users/nikolay/projects/agmem/ \
  "${HOST}:${REMOTE_DIR}/"

echo ""
echo "Committing and pushing on remote..."
ssh "${HOST}" "
  cd ${REMOTE_DIR} &&
  git add -A &&
  git commit -m 'sync from local' &&
  git push
"

echo "Done."
