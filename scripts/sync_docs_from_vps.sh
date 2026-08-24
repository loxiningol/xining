#!/usr/bin/env bash
# Pull production documentation from VPS into local workspace.
# Prod host migrated: Vultr 64.176.47.192 (offline) → Aliyun 47.81.25.235.
set -euo pipefail

SSH_KEY="${SSH_KEY:-/Users/lele/Documents/Codex/2026-07-12/ru-g/work/ssh/vultr_codex_ed25519}"
HOST="${DEPLOY_HOST:-admin@47.81.25.235}"
LOCAL_ROOT="${LOCAL_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
STAMP=$(date +%Y%m%d_%H%M%S)
LOG="$LOCAL_ROOT/docs/_sync_logs/doc_sync_${STAMP}.log"

mkdir -p "$LOCAL_ROOT/docs/_sync_logs"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$LOG"; }

log "sync start host=$HOST local=$LOCAL_ROOT"

# 1) Authoritative /root/docs/
rsync -avz -e "ssh -i $SSH_KEY -o BatchMode=yes" \
  --rsync-path="sudo rsync" \
  "$HOST:/root/docs/" "$LOCAL_ROOT/docs/" \
  2>&1 | tee -a "$LOG"

# 2) Phase acceptance bundles
for d in p5_verification p6_quality p7_structural p7_1_closure p7_1_formal; do
  mkdir -p "$LOCAL_ROOT/$d"
  rsync -avz -e "ssh -i $SSH_KEY -o BatchMode=yes" \
    --rsync-path="sudo rsync" \
    "$HOST:/root/$d/" "$LOCAL_ROOT/$d/" \
    2>&1 | tee -a "$LOG"
done

# 3) Root-level panorama / audit reports (tar avoids rsync hang on deep trees)
ROOT_MD=(
  QUANT_SYSTEM_PANORAMA_AND_MULTI_AI_INTEGRATION_REPORT.md
  CREATION_PIPELINE_OUTPUT_RECOVERY_REPORT.md
  READONLY_AUDIT_STRATEGY_LIFECYCLE_MAPPING.md
  WINDTALKER_PHASE2_final_report.md
  WINDTALKER_PHASE5_final_report.md
  WINDTALKER_PLAN_EXECUTION_AND_LANDING_STATUS_REPORT.md
  STRATEGY_ECOSYSTEM_V2.md
  MACRO_SFP_FEATURES.md
)
TAR_ARGS=()
for f in "${ROOT_MD[@]}"; do TAR_ARGS+=("$f"); done
ssh -i "$SSH_KEY" -o BatchMode=yes "$HOST" \
  "sudo tar czf - -C /root ${TAR_ARGS[*]}" \
  | tar xzf - -C "$LOCAL_ROOT" 2>&1 | tee -a "$LOG"

log "sync done log=$LOG"
