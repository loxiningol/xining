#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import re
import py_compile

p = Path("/root/frost3_sol15m_breakout_run.py")
t = p.read_text()

# Remove bad atr14>h1_atr14 from sketches/docs
t = t.replace("atr14>h1_atr14; ", "")
t = t.replace("atr14>h1_atr14", "atr14_noncollapse_proxy")

t = t.replace(
    "极静盘用 atr14_noncollapse_proxy 过滤。",
    "极静盘用 atr14相对近端不塌缩（atr14>=atr14 offset8）+破高/z扩张代理过滤。",
)
t = t.replace(
    "DSL无atr_pct；用 atr14_noncollapse_proxy 作为『非极静/波动充足』代理，",
    "DSL无atr_pct；用 atr14>=atr14(offset=8) 作为『波动未塌缩/释放中』代理，",
)
t = t.replace(
    "入场过滤 atr14_noncollapse_proxy 排除极静噪声盘",
    "入场过滤 atr14相对8根前不塌缩 + 破高/z扩张，排除极静噪声盘",
)
t = t.replace(
    "用『非极静』代理 atr14_noncollapse_proxy*0.35 或文档化+",
    "用『波动未塌缩』代理 atr14>=atr14(offset=8) + 文档化+",
)
t = t.replace(
    '"atr_stop_proxy": "atr14_noncollapse_proxy (need atr_pct>=0.60% for 0.9% stop <=1.5ATR)",',
    '"atr_stop_proxy": "atr14>=atr14@offset8 + breakout/z expansion '
    '(need atr_pct>=0.60% for 0.9% stop <=1.5ATR)",',
)
t = t.replace(
    "方案须写明 stop_vs_atr 与DSL代理（如 atr14_noncollapse_proxy）",
    "方案须写明 stop_vs_atr 与DSL代理（atr14>=atr14 offset8 + 破高）",
)

# Fix default entry leaves that still compare to h1_atr14
old = '{"left": {"feature": "atr14"}, "op": "gt", "right": {"feature": "h1_atr14"}},'
new = '{"left": {"feature": "atr14"}, "op": "gte", "right": {"feature": "atr14", "offset": 8}},'
count = t.count(old)
t = t.replace(old, new)
print("default atr leaf replacements", count)

# Inject ATR leaf enforcer once
if "Always enforce ATR-stop" not in t:
    inject = '''
    # Always enforce ATR-stop adequacy proxy (vol not collapsing)
    atr_leaf = {
        "left": {"feature": "atr14"},
        "op": "gte",
        "right": {"feature": "atr14", "offset": 8},
    }
    if not any(
        ((x.get("left") or {}).get("feature") == "atr14"
         and int((x.get("right") or {}).get("offset") or 0) == 8)
        for x in entry
    ):
        entry.append(atr_leaf)

'''
    m2 = '    if direction == "long":\n        exit_any = ['
    if m2 not in t:
        raise SystemExit("inject point missing")
    t = t.replace(m2, inject + m2)
    print("injected atr leaf enforcer")
else:
    print("enforcer already present")

t = t.replace(
    "6) DSL无volume：量能用 cci+macd_stick+z20 代理（见 volume_feature_note）\\n6b)",
    "6) DSL无volume：量能用 cci+macd_stick+z20 代理（见 volume_feature_note）\n6b)",
)

new_note = '''            "note_zh": (
                "SOL15m可用h1_ema19/53；无volume；atr_pct中位≈%.3f%%；"
                "0.9%%止损≈%.2f×ATR；atr_pct≥0.60%%满足率≈%.1f%%；破高≈%.2f%%"
                % (100.0 * float(atr_pct.quantile(0.5)),
                   float((0.009 / atr_pct.replace(0, float("nan"))).median()),
                   100.0 * float((atr_pct >= 0.006).mean()),
                   100.0 * float((df["close"] > df["prev_high20"]).mean()))
            ),'''
m = re.search(r'"note_zh": \([\s\S]*?\),', t)
if m:
    print("FOUND note_zh:\n", m.group(0)[:300])
    t = t[: m.start()] + new_note + t[m.end() :]
    print("note_zh replaced")
else:
    print("WARN no note_zh")

# Clean leftover placeholder tokens in sketches
t = t.replace("atr14_noncollapse_proxy; ", "")
t = t.replace("; atr14_noncollapse_proxy", "")
t = t.replace("atr14_noncollapse_proxy", "atr14>=offset8_proxy")

p.write_text(t)
py_compile.compile(str(p), doraise=True)
print("COMPILE_OK")
assert "Always enforce ATR-stop" in t
assert '"offset": 8' in t
assert "h1_atr14" not in t.split("entry = [")[1].split("]")[0] if "entry = [" in t else True
print("OK")
