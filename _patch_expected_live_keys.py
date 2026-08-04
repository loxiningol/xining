#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function

import importlib
import shutil
import sys
import time
from pathlib import Path

p = Path("/root/web_server.py")
text = p.read_text(encoding="utf-8")
old = (
    "    # Expected current live openable set (ADA/LTC/NG 5m + XRP 15m).\n"
    "    expected_live_keys = {\n"
    '        "codex0725t3_ada5m_trendpb_r42_z2p3_h14",\n'
    '        "ltc5_exhaustion_fade_short_ai",\n'
    '        "ng5_exhaustion_fade_short_ai",\n'
    '        "frost_xrp_rescue_h20_t45",\n'
    "    }"
)
new = (
    "    # Expected current live openable set:\n"
    "    # ADA/LTC/NG 5m + XRP 15m + BTC 1h (frost3 xrpport exhaustion_fade).\n"
    "    expected_live_keys = {\n"
    '        "codex0725t3_ada5m_trendpb_r42_z2p3_h14",\n'
    '        "ltc5_exhaustion_fade_short_ai",\n'
    '        "ng5_exhaustion_fade_short_ai",\n'
    '        "frost_xrp_rescue_h20_t45",\n'
    '        "frost3_btc1h_xrpport_exhaustion_fade_slope",\n'
    "    }"
)
if "frost3_btc1h_xrpport_exhaustion_fade_slope" in text and "expected_live_keys" in text:
    # Check whether it is already inside the expected set block.
    i = text.find("expected_live_keys")
    block = text[i:i + 400]
    if "frost3_btc1h_xrpport_exhaustion_fade_slope" in block:
        print("already_patched")
    else:
        print("btc key exists elsewhere but expected block stale")
        print(block)
        if old not in text:
            raise SystemExit("cannot patch: old block missing")
        bak = Path("/root/web_server.py.bak_expected_live_%s" % time.strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(str(p), str(bak))
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        print("patched", bak)
elif old in text:
    bak = Path("/root/web_server.py.bak_expected_live_%s" % time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(str(p), str(bak))
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    print("patched", bak)
else:
    i = text.find("expected_live_keys")
    print("CURRENT\n", text[i - 80:i + 350] if i >= 0 else "missing")
    raise SystemExit("unexpected web_server.py content")

# verify file content
text2 = p.read_text(encoding="utf-8")
i = text2.find("expected_live_keys")
print(text2[i:i + 320])

# restart done by caller; here just validate via fresh import in subprocess style
sys.path.insert(0, "/root")
if "web_server" in sys.modules:
    del sys.modules["web_server"]
import web_server

d = web_server.build_process_status_payload()
print("summary", d.get("summary"), d.get("healthy_count"), "/", d.get("total_count"))
for c in d.get("components") or []:
    if "策略监测" in str(c.get("name") or ""):
        print("auto_strategies healthy=", c.get("healthy"))
        print((c.get("summary") or "")[:400])
