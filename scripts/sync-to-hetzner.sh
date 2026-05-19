#!/bin/bash
set -euo pipefail

HOST="${1:-hetzner}"
REMOTE_DIR="/home/nikolay/projects/agmem"

echo "Syncing source to ${HOST}:${REMOTE_DIR} ..."

# Sync everything from the git-tracked tree + .agmem/ config
rsync -avz --progress \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='*.egg-info/' \
  --exclude='.DS_Store' \
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
