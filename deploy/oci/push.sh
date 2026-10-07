#!/usr/bin/env bash
# Run from Git Bash on the laptop, inside the project folder:
#
#   bash deploy/oci/push.sh                 # first time: full install on the server
#   bash deploy/oci/push.sh                 # later: code update only (server data is kept)
#   bash deploy/oci/push.sh other-ssh-host  # if your ssh alias is not manipraocispaces_vm
#
# The project is streamed over SSH (nothing with secrets is left on disk). On the first run it copies
# everything incl. .env, configs and the price cache, then runs deploy/oci/setup.sh on the server.
# On later runs it copies CODE ONLY: the server's .env, trader.yaml, config.yaml, journal, token,
# logs, backups and cache are never overwritten.
set -euo pipefail

HOST="${1:-manipraocispaces_vm}"
SSH="${PG_SSH:-ssh}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NAME="$(basename "$HERE")"
PARENT="$(dirname "$HERE")"
COMMON=(--exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude=logs --exclude=backups
        --exclude=STOP --exclude=pg.tgz --exclude=.setup-done)

echo "Project: $HERE  ->  $HOST:~/$NAME"
if $SSH "$HOST" "test -f ~/$NAME/.setup-done"; then
  echo "Server already set up: updating code only (server data and settings are kept)."
  tar czf - -C "$PARENT" "${COMMON[@]}" \
      --exclude="$NAME/.env" --exclude="$NAME/.token_cache.json" --exclude="$NAME/.token_cache.json.*" --exclude="$NAME/trader.yaml" \
      --exclude="$NAME/config.yaml" --exclude="$NAME/state.json" --exclude="$NAME/trader/state" \
      --exclude="$NAME/cache" --exclude="$NAME/results" --exclude="$NAME/research/data" "$NAME" \
    | $SSH "$HOST" "tar xzf - -C ~"
  $SSH "$HOST" "cd ~/$NAME && sed -i 's/\r\$//' deploy/oci/*.sh deploy/oci/crontab && chmod +x deploy/oci/*.sh \
      && .venv/bin/python -m pip install -q -r requirements.txt -r deploy/oci/requirements-server.txt \
      && sed \"s#__APP__#\$HOME/$NAME#g\" deploy/oci/crontab | crontab - \
      && .venv/bin/python -m pytest -q tests 2>&1 | tail -2"
  echo "Update done."
else
  echo "First install (or an earlier setup did not finish): copying the project (about 130 MB, a few minutes)..."
  tar czf - -C "$PARENT" "${COMMON[@]}" "$NAME" | $SSH "$HOST" "tar xzf - -C ~"
  echo "Copied. Running the server setup (5-10 minutes)..."
  $SSH "$HOST" "bash ~/$NAME/deploy/oci/setup.sh"
fi
