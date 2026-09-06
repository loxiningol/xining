#!/usr/bin/env bash
# Write /root/deployed.sha on VPS after a deliberate deploy.
# Usage: ./scripts/write_deployed_sha.sh [sha-or-tag] [user@host] [ssh_key]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REF="${1:-HEAD}"
HOST="${2:-${KDH_THIN_HOST:-admin@47.81.25.235}}"
KEY="${3:-${KDH_THIN_KEY:-$HOME/Documents/Codex/2026-07-12/ru-g/work/ssh/vultr_codex_ed25519}}"

SHA="$(git -C "$ROOT" rev-parse "$REF")"
SHORT="$(git -C "$ROOT" rev-parse --short "$SHA")"

ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$KEY" "$HOST" \
  "echo '$SHA' | sudo tee /root/deployed.sha >/dev/null && sudo chmod 644 /root/deployed.sha && echo written:\$(sudo cat /root/deployed.sha)"

echo "OK: /root/deployed.sha <= $SHA ($SHORT) from ref $REF"
