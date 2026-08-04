#!/usr/bin/env bash
# Atomic STEP A deploy — does NOT touch formal_daemon / live SL-TP chain.
set -euo pipefail
SSH_KEY="${SSH_KEY:-/Users/lele/Documents/Codex/2026-07-12/ru-g/work/ssh/vultr_codex_ed25519}"
HOST="${HOST:-root@64.176.47.192}"
LOCAL_ROOT="${LOCAL_ROOT:-/Users/lele/Documents/Codex/2026-07-12/ru-g/work/intraday_live_v1_20260720}"
STAMP=$(date +%Y%m%d_%H%M%S)
BACKUP="/root/backups/step_a_strategy_creation_${STAMP}"

ssh -i "$SSH_KEY" -o BatchMode=yes "$HOST" bash -s <<EOF
set -euo pipefail
BACKUP="$BACKUP"
mkdir -p "\$BACKUP" /root/docs /root/dual_engine_workflow_v2
# Snapshot files we will replace
for f in \
  /root/auto_trade_dual_engine_factory.py \
  /root/dual_engine_workflow_v2/__init__.py \
  /root/dual_engine_workflow_v2/config.py \
  /root/dual_engine_workflow_v2/pipeline.py
 do
  if [[ -f "\$f" ]]; then
    cp -a "\$f" "\$BACKUP/"
  fi
done
# Snapshot whole package tree lightly
if [[ -d /root/dual_engine_workflow_v2 ]]; then
  tar -C /root -czf "\$BACKUP/dual_engine_workflow_v2_pre.tgz" dual_engine_workflow_v2 || true
fi
# Production evidence before
{
  echo "backup=\$BACKUP"
  echo "stamp=$STAMP"
  systemctl is-active qiyu-formal-auto-trade.service || true
  systemctl list-units --type=service --state=running | grep -i formal || true
  python3 -c "import json;print(json.load(open('/root/auto_trade/dual_engine/workflow_flags.json')))" || true
} > "\$BACKUP/pre_deploy_evidence.txt"
cat > "\$BACKUP/ROLLBACK.sh" <<'ROL'
#!/usr/bin/env bash
set -euo pipefail
HERE=\$(cd "\$(dirname "\$0")" && pwd)
# Restore package
if [[ -f "\$HERE/dual_engine_workflow_v2_pre.tgz" ]]; then
  tar -C /root -xzf "\$HERE/dual_engine_workflow_v2_pre.tgz"
fi
for f in auto_trade_dual_engine_factory.py; do
  if [[ -f "\$HERE/\$f" ]]; then
    cp -a "\$HERE/\$f" /root/\$f
  fi
done
# Do NOT stop formal daemons; do NOT alter live configs
echo "STEP A rollback restored code snapshots from \$HERE"
systemctl is-active qiyu-formal-auto-trade.service || true
ROL
chmod +x "\$BACKUP/ROLLBACK.sh"
echo "BACKUP_READY \$BACKUP"
EOF

# Sync STEP A modules
rsync -az -e "ssh -i $SSH_KEY -o BatchMode=yes" \
  "$LOCAL_ROOT/dual_engine_workflow_v2/" \
  "$HOST:/root/dual_engine_workflow_v2/"

rsync -az -e "ssh -i $SSH_KEY -o BatchMode=yes" \
  "$LOCAL_ROOT/auto_trade_dual_engine_factory.py" \
  "$HOST:/root/auto_trade_dual_engine_factory.py"

rsync -az -e "ssh -i $SSH_KEY -o BatchMode=yes" \
  "$LOCAL_ROOT/tests_workflow_v2/test_step_a_acceptance.py" \
  "$HOST:/root/tests_workflow_v2/test_step_a_acceptance.py"

# Post flags note (keep creation_entry=v2; step_a routes through it)
ssh -i "$SSH_KEY" -o BatchMode=yes "$HOST" bash -s <<EOF
set -euo pipefail
BACKUP="$BACKUP"
python3 - <<'PY'
import json, time
from pathlib import Path
p=Path("/root/auto_trade/dual_engine/workflow_flags.json")
flags=json.loads(p.read_text())
notes=list(flags.get("notes") or [])
notes.append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": "STEP A modules deployed; v2 entry routes to step_a pipeline"})
flags["notes"]=notes[-50:]
flags["step_a_deployed"]=True
flags["step_a_code_version"]="step_a_strategy_creation_20260726"
flags["updated_at"]=time.strftime("%Y-%m-%d %H:%M:%S")
tmp=p.with_suffix(".tmp")
tmp.write_text(json.dumps(flags, ensure_ascii=False, indent=2))
tmp.replace(p)
print("flags_updated")
PY
mkdir -p /root/tests_workflow_v2 /root/auto_trade/dual_engine/workflow_v2
# ensure step_a dirs
python3 - <<'PY'
from dual_engine_workflow_v2.step_a_config import ensure_step_a_dirs, STEP_A_CODE_VERSION
ensure_step_a_dirs()
print("step_a_dirs_ok", STEP_A_CODE_VERSION)
PY
# unit tests on server
cd /root && python3 tests_workflow_v2/test_step_a_acceptance.py | tee "\$BACKUP/unit_tests.out"
# production evidence after — formal still running
{
  echo "=== formal services ==="
  systemctl list-units --type=service --state=running | grep -i formal || true
  echo "=== open hunter / human confirm ==="
  systemctl list-timers --all | grep -iE "open|human|evolve|rating" || true
  echo "=== import check ==="
  python3 - <<'PY'
import dual_engine_workflow_v2 as w
print("entry", w.creation_entry())
print("has_step_a", hasattr(w, "start_creation_task_step_a"))
print("STEP_A", w.STEP_A_CODE_VERSION)
PY
} | tee "\$BACKUP/post_deploy_evidence.txt"
echo "DEPLOY_OK \$BACKUP"
EOF

echo "LOCAL_DEPLOY_SCRIPT_DONE backup=$BACKUP"
