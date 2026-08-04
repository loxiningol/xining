#!/bin/bash
# ADA SL 0.006 -> 0.009 MIGRATE — REQUIRES HUMAN CONFIRMATION. DO NOT auto-run.
set -euo pipefail
CFG=/root/auto_trade/formal_daemon_config_ada_5m.json
BK=/root/backups/ada_sl_migrate_$(date +%Y%m%d_%H%M%S)
mkdir -p "$BK"
cp -a "$CFG" "$BK/"
python3 - <<'PY'
import json
from pathlib import Path
p = Path("/root/auto_trade/formal_daemon_config_ada_5m.json")
d = json.loads(p.read_text())
assert d.get("stop_loss_pct") in (0.006, 0.009)
d["stop_loss_pct"] = 0.009
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
print("migrated to", d["stop_loss_pct"])
PY
systemctl restart qiyu-formal-auto-trade-ada-5m
systemctl is-active qiyu-formal-auto-trade-ada-5m
echo "VERIFY next ADA order SL distance ~= 0.009"
