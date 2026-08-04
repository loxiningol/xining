#!/usr/bin/env bash
# Rollback L2/L3 positive-E creation-input peg fix on live host.
set -euo pipefail
BACKUP=/root/backups/l2_l3_creation_input_peg_20260727_115908
KEY=/Users/lele/Documents/Codex/2026-07-12/ru-g/work/ssh/vultr_codex_ed25519
ssh -i "$KEY" -o BatchMode=yes root@64.176.47.192 "bash $BACKUP/ROLLBACK.sh"
