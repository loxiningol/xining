#!/usr/bin/env bash
# Ensure a persistent host swapfile (default 2Gi) — cushion for 1.8Gi RAM hosts.
# Idempotent: safe to re-run.
set -euo pipefail

SWAPFILE="${QIYU_SWAPFILE:-/swapfile}"
SWAP_SIZE_MIB="${QIYU_SWAP_SIZE_MIB:-2048}"

if swapon --show --noheadings 2>/dev/null | grep -q .; then
  echo "ensure_host_swap: swap already active:"
  swapon --show
  free -h | sed -n '1,3p'
  exit 0
fi

if [[ -f "$SWAPFILE" ]]; then
  echo "ensure_host_swap: found existing $SWAPFILE; enabling"
else
  echo "ensure_host_swap: creating ${SWAP_SIZE_MIB}MiB at $SWAPFILE"
  # Prefer fallocate; fall back to dd if filesystem rejects it for swap.
  if ! fallocate -l "${SWAP_SIZE_MIB}M" "$SWAPFILE" 2>/dev/null; then
    dd if=/dev/zero of="$SWAPFILE" bs=1M count="$SWAP_SIZE_MIB" status=progress
  fi
  chmod 600 "$SWAPFILE"
  mkswap "$SWAPFILE"
fi

swapon "$SWAPFILE"

fstab_line="$SWAPFILE none swap sw 0 0"
if ! grep -qE "^[[:space:]]*${SWAPFILE}[[:space:]]" /etc/fstab 2>/dev/null; then
  echo "$fstab_line" >>/etc/fstab
  echo "ensure_host_swap: appended fstab entry"
else
  echo "ensure_host_swap: fstab already references $SWAPFILE"
fi

swapon --show
free -h | sed -n '1,3p'
echo "ensure_host_swap: ok"
