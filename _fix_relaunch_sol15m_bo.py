#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix SOL15m breakout runner: strip illegal DSL fields; force fallback book; relaunch."""
from pathlib import Path
import re
import py_compile
import os
import signal
import time
import json
import sys

sys.path.insert(0, "/root")

p = Path("/root/frost3_sol15m_breakout_run.py")
t = p.read_text()

# Drop description line in dsl dict if still present
t = t.replace(
    '        "description": str(row.get("thesis") or "SOL15m trend_breakout")[:160],\n',
    "",
)

# Remove meta block inside build_sol15m_breakout_dsl
t = re.sub(
    r'\n\s*"meta": \{[\s\S]*?\},\n(\s*"schema")',
    r"\n\1",
    t,
    count=1,
)

# Ensure strip before ensure_dsl in builder
idx = t.find("def build_sol15m_breakout_dsl")
needle = "return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)"
j = t.find(needle, idx)
if j < 0:
    raise SystemExit("ensure_dsl not found in builder")
if "dsl.pop(_drop" not in t[idx : j + 80]:
    inject = (
        "\n    for _drop in (\"meta\", \"description\", \"hypothesis\", \"notes\"):\n"
        "        dsl.pop(_drop, None)\n"
        "    return f2.ensure_dsl(dsl, SYMBOL, TIMEFRAME)"
    )
    t = t[:j] + inject + t[j + len(needle) :]
    print("injected illegal-field strip")
else:
    print("strip already present")

# Force using fallback variant for executable DSL (GLM plan still saved as artifacts)
# Replace make_book(pick) section to rebuild from fallback rank1 with GLM threshold overlays if any
old_main_book = "    book = make_book(pick, 1)\n    write_art(\"hypothesis_book_v0\", book)"
new_main_book = '''    # Executable book from fallback skeleton (GLM plan kept in artifacts; avoids invalid sketch ops)
    fb = fallback_param_plan(packet)
    fb_row = None
    for row in fb.get("variants") or []:
        if int(row.get("rank") or 0) == 1:
            fb_row = row
            break
    if fb_row is None:
        fb_row = (fb.get("variants") or [pick])[0]
    # Overlay soft numeric hints from GLM pick when present
    for k in ("vol_contraction", "volume_proxy", "ema_period"):
        if pick.get(k):
            fb_row[k] = pick.get(k)
    # Prefer GLM entry_sketch only if fully parseable to >=5 leaves without exotic tokens
    sketch = str(pick.get("entry_sketch") or "")
    if sketch and "[" not in sketch and "offset" not in sketch.lower() and "entry+" not in sketch:
        fb_row["entry_sketch"] = sketch
        if pick.get("exit_sketch") and "entry+" not in str(pick.get("exit_sketch")):
            fb_row["exit_sketch"] = pick.get("exit_sketch")
    book = make_book(fb_row, 1)
    write_art("hypothesis_book_v0", book)
    write_art("executable_vs_glm_pick", {
        "executable_entry": book.get("entry_sketch") or fb_row.get("entry_sketch"),
        "glm_entry": pick.get("entry_sketch"),
        "note": "fallback skeleton used for DSL validity; GLM plan retained",
    })'''
if old_main_book in t:
    t = t.replace(old_main_book, new_main_book)
    print("main book path patched")
else:
    print("WARN main book anchor miss")

p.write_text(t)
py_compile.compile(str(p), doraise=True)
print("COMPILE_OK")

import frost3_sol15m_breakout_run as m
import auto_trade_strategy_dsl as dsl
import frost2_action_run as f2

f2.QUICK_WF_POS = 7
rep = m.fallback_param_plan({
    "sol15m_vol": m.load_sol15m_vol_snapshot(),
    "breakout_freezer": m.load_breakout_freezer(),
})
book = m.make_book(rep["variants"][0], 1)
print("dsl keys", sorted(book["dsl"].keys()))
dsl.validate_strategy(book["dsl"])
print("VALIDATE_OK", book["dsl"]["key"], "n_entry", len(book["dsl"]["entry"]["all"]))

# Kill prior runner if any
os.system("pkill -f '/root/frost3_sol15m_breakout_run.py' 2>/dev/null || true")
time.sleep(1)

# Clear prior breakout json artifacts but keep log append
import glob
for fp in glob.glob("/root/auto_trade/dual_engine/frost3_sol15m_breakout_*.json"):
    try:
        os.remove(fp)
    except Exception:
        pass

# Relaunch
os.system(
    "nohup python3 -u /root/frost3_sol15m_breakout_run.py "
    ">> /root/auto_trade/dual_engine/frost3_sol15m_breakout_run.log 2>&1 &"
)
time.sleep(4)
print("relaunch done")
print(open("/root/auto_trade/dual_engine/frost3_sol15m_breakout_run.log").read()[-800:])
