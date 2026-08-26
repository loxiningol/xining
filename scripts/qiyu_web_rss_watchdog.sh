#!/usr/bin/env bash
# Soft-restart qiyu-web before cgroup/global OOM hard-kill.
# Threshold default 750MiB (MemoryMax is 1200M). Cooldown avoids restart storms.
set -euo pipefail

UNIT="${QIYU_WEB_UNIT:-qiyu-web.service}"
THRESHOLD_MIB="${QIYU_WEB_RSS_THRESHOLD_MIB:-750}"
COOLDOWN_SEC="${QIYU_WEB_RSS_COOLDOWN_SEC:-300}"
STATE_DIR="${QIYU_WEB_WATCH_STATE_DIR:-/root/auto_trade}"
STATE_FILE="${STATE_DIR}/qiyu_web_rss_watchdog.state"
LOCK_FILE="${STATE_DIR}/qiyu_web_rss_watchdog.lock"

mkdir -p "$STATE_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "qiyu-web-rss-watch: another instance holds lock; skip"
  exit 0
fi

if ! systemctl is-active --quiet "$UNIT"; then
  echo "qiyu-web-rss-watch: $UNIT not active; skip"
  exit 0
fi

# Prefer cgroup MemoryCurrent (matches systemd accounting / OOM charge).
mem_bytes="$(systemctl show "$UNIT" -p MemoryCurrent --value 2>/dev/null || true)"
if [[ -z "${mem_bytes}" || "${mem_bytes}" == "[not set]" || "${mem_bytes}" == "0" ]]; then
  pid="$(systemctl show "$UNIT" -p MainPID --value 2>/dev/null || true)"
  if [[ -n "${pid}" && "${pid}" != "0" && -r "/proc/${pid}/status" ]]; then
    mem_kib="$(awk '/^VmRSS:/{print $2; exit}' "/proc/${pid}/status")"
    mem_bytes=$(( ${mem_kib:-0} * 1024 ))
  else
    echo "qiyu-web-rss-watch: cannot read memory; skip"
    exit 0
  fi
fi

mem_mib=$(( mem_bytes / 1024 / 1024 ))
echo "qiyu-web-rss-watch: ${UNIT} memory=${mem_mib}MiB threshold=${THRESHOLD_MIB}MiB"

if (( mem_mib < THRESHOLD_MIB )); then
  exit 0
fi

now="$(date +%s)"
last=0
if [[ -f "$STATE_FILE" ]]; then
  last="$(awk -F= '/^last_restart_ts=/{print $2; exit}' "$STATE_FILE" 2>/dev/null || echo 0)"
fi
if [[ -n "${last}" && "${last}" =~ ^[0-9]+$ ]] && (( now - last < COOLDOWN_SEC )); then
  echo "qiyu-web-rss-watch: over threshold but cooldown $((now - last))s < ${COOLDOWN_SEC}s; skip"
  exit 0
fi

echo "qiyu-web-rss-watch: soft-restart ${UNIT} (memory ${mem_mib}MiB >= ${THRESHOLD_MIB}MiB)"
{
  echo "last_restart_ts=${now}"
  echo "last_restart_memory_mib=${mem_mib}"
  echo "last_restart_iso=$(date -Iseconds)"
} >"$STATE_FILE"

systemctl restart "$UNIT"
echo "qiyu-web-rss-watch: restart issued"
