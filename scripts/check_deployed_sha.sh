#!/usr/bin/env bash
# Compare local git SHA (or QIYU_EXPECT_SHA) with VPS /root/deployed.sha.
# Usage: ./scripts/check_deployed_sha.sh [user@host] [ssh_key]
set -euo pipefail

HOST="${1:-${KDH_THIN_HOST:-admin@47.81.25.235}}"
KEY="${2:-${KDH_THIN_KEY:-$HOME/Documents/Codex/2026-07-12/ru-g/work/ssh/vultr_codex_ed25519}}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -n "${QIYU_EXPECT_SHA:-}" ]]; then
  LOCAL="${QIYU_EXPECT_SHA}"
else
  LOCAL="$(git -C "$ROOT" rev-parse HEAD)"
fi
LOCAL_SHORT="$(git -C "$ROOT" rev-parse --short "$LOCAL" 2>/dev/null || echo "$LOCAL")"

REMOTE="$(
  ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$KEY" "$HOST" \
    'sudo cat /root/deployed.sha 2>/dev/null || cat /root/deployed.sha 2>/dev/null || true'
)"
REMOTE="$(echo "$REMOTE" | tr -d '[:space:]')"

echo "local:  $LOCAL ($LOCAL_SHORT)"
echo "remote: ${REMOTE:-<missing>}"

if [[ -z "$REMOTE" ]]; then
  echo "FAIL: /root/deployed.sha missing on $HOST" >&2
  exit 2
fi
if [[ "$REMOTE" != "$LOCAL" && "$REMOTE" != "$LOCAL_SHORT" ]]; then
  # allow remote full vs local short and vice versa
  REMOTE_FULL="$(git -C "$ROOT" rev-parse "$REMOTE" 2>/dev/null || true)"
  if [[ -n "$REMOTE_FULL" && "$REMOTE_FULL" == "$LOCAL" ]]; then
    echo "OK: deployed.sha matches HEAD"
    exit 0
  fi
  echo "FAIL: mismatch (local HEAD vs /root/deployed.sha)" >&2
  exit 1
fi
echo "OK: deployed.sha matches HEAD"
