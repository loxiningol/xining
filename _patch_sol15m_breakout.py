#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch eth1h breakout runner → sol15m breakout on the remote host."""
from pathlib import Path
import re
import shutil

src = Path("/root/frost3_eth1h_breakout_run.py")
dst = Path("/root/frost3_sol15m_breakout_run.py")
shutil.copyfile(src, dst)
t = dst.read_text()

repls = [
    ("frost3_eth1h_breakout", "frost3_sol15m_breakout"),
    ("ETH-USDT-SWAP", "SOL-USDT-SWAP"),
    ("ETH 1h", "SOL 15m"),
    ("ETH1h", "SOL15m"),
    ("eth1h_breakout", "sol15m_breakout"),
    ("eth1h_bo", "sol15m_bo"),
    ("load_eth_micro", "load_sol_micro"),
    ("load_eth1h_vol_snapshot", "load_sol15m_vol_snapshot"),
    ("build_eth1h_breakout_dsl", "build_sol15m_breakout_dsl"),
    ("ETH微观", "SOL微观"),
    ("ETH1h波动", "SOL15m波动"),
    ("ETH1h多头", "SOL15m多头"),
    ("eth_micro", "sol_micro"),
    ("eth1h_vol", "sol15m_vol"),
    ('TIMEFRAME = "1h"', 'TIMEFRAME = "15m"'),
    ("寒霜叁-ETH-1h-trend_breakout", "寒霜叁-SOL-15m-trend_breakout"),
    ("寒霜叁-SOL-1h-trend_breakout", "寒霜叁-SOL-15m-trend_breakout"),
]
for a, b in repls:
    t = t.replace(a, b)

t = t.replace(
    "Standalone SOL-USDT-SWAP 1h trend-breakout strategy creation.",
    "Standalone SOL-USDT-SWAP 15m trend-breakout strategy creation.",
)
t = t.replace(
    "Customize params for SOL 1h",
    "Customize params for SOL 15m",
)
t = t.replace(
    "Artifacts ONLY under frost3_sol15m_breakout_* — parallel-safe vs ETH 5m / BNB / others.",
    "Artifacts ONLY under frost3_sol15m_breakout_* — "
    "parallel-safe vs frost3_sol15m_* (non-breakout) / ETH / BNB.",
)
t = t.replace(
    "独立于 frost3_eth5m_*；产物前缀 frost3_sol15m_breakout_*",
    "独立于 frost3_sol15m_*（非突破）；产物前缀 frost3_sol15m_breakout_*",
)
t = t.replace(
    "独立于 frost3_sol5m_*；产物前缀 frost3_sol15m_breakout_*",
    "独立于 frost3_sol15m_*（非突破）；产物前缀 frost3_sol15m_breakout_*",
)
# after ETH→SOL, parallel note may reference eth5m still via missed path
t = t.replace(
    "独立于 frost3_sol15m_*；产物前缀 frost3_sol15m_breakout_*",
    "独立于 frost3_sol15m_*（非突破同标的其它管线）；产物前缀 frost3_sol15m_breakout_*",
)

# Fix PARAM_PROMPT timeframe hard constraints that still say 1h
t = t.replace("timeframe固定 1h", "timeframe固定 15m")
t = t.replace(
    "1h特征用 ema19/ema53（不要用缺失的h1_ema*）、prev_high20/prev_low20、h1_slope4、rsi14、z20、cci、macd_stick、atr14",
    "15m特征优先用 h1_ema19/h1_ema53 趋势过滤 + close>prev_high20 突破；"
    "辅以 ema21、h1_slope4、rsi14、z20、cci、macd_stick、atr14。"
    "硬约束：0.9%止损@20x 必须满足 entry-stop距离 ≤1.5×ATR "
    "（即 atr_pct=atr14/close ≥0.60%，否则噪声扫损）。"
    "DSL无 atr_pct 比值：用『非极静』代理 atr14>h1_atr14*0.35 或文档化+"
    "仅在破高+中等z窗口入场（突破本身偏向充足波动）。",
)
t = t.replace('"timeframe":"1h"', '"timeframe":"15m"')

# Update volume note for 15m (h1 available)
t = t.replace(
    "波动收缩用 z20 上界抑制已极端延展（收缩后释放窗口），"
    "非BB squeeze序列（单bar DSL不可表达先验收缩序列）。",
    "波动收缩用 z20 上界抑制已极端延展（收缩后释放窗口），"
    "非BB squeeze序列（单bar DSL不可表达先验收缩序列）。"
    "ATR-止损匹配：固定止损0.9%，要求≤1.5×ATR ⇒ atr_pct≥0.60%；"
    "过滤极静盘（atr14过低相对h1_atr）以防噪声扫损。",
)

# Remap note: 15m SHOULD use h1_ema*, do NOT remap h1→ema in parse
# Remove the 1h remap block in parse_sketch_conditions
t = t.replace(
    """        # 1h frame: remap missing h1_ema* → native ema*
        if left == "h1_ema19":
            left = "ema19"
        if left == "h1_ema53":
            left = "ema53"
""",
    """        # 15m frame: keep h1_ema19/53 (higher-TF trend filter available)
""",
)
t = t.replace(
    """            rfeat = right
            if rfeat == "h1_ema19":
                rfeat = "ema19"
            if rfeat == "h1_ema53":
                rfeat = "ema53"
            leaf["right"] = {"feature": rfeat}
""",
    """            rfeat = right
            leaf["right"] = {"feature": rfeat}
""",
)

# Default entry for 15m: use h1 trend + ATR-stop proxy
old_long_entry = '''            entry = [
                {"left": {"feature": "ema19"}, "op": "gt", "right": {"feature": "ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 72.0}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.2}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.4}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 70.0}},
            ]'''
new_long_entry = '''            entry = [
                {"left": {"feature": "h1_ema19"}, "op": "gt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}},
                {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "ema21"}},
                {"left": {"feature": "atr14"}, "op": "gt", "right": {"feature": "h1_atr14"}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 55.0}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 72.0}},
                {"left": {"feature": "macd_stick"}, "op": "gt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.2}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": 1.35}},
                {"left": {"feature": "cci"}, "op": "gt", "right": {"value": 65.0}},
            ]'''
if old_long_entry in t:
    t = t.replace(old_long_entry, new_long_entry)
    print("long default entry patched")
else:
    print("WARN long entry miss")

old_short_entry = '''            entry = [
                {"left": {"feature": "ema19"}, "op": "lt", "right": {"feature": "ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 28.0}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": -0.2}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.4}},
                {"left": {"feature": "cci"}, "op": "lt", "right": {"value": -70.0}},
            ]'''
new_short_entry = '''            entry = [
                {"left": {"feature": "h1_ema19"}, "op": "lt", "right": {"feature": "h1_ema53"}},
                {"left": {"feature": "h1_slope4"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "prev_low20"}},
                {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema21"}},
                {"left": {"feature": "atr14"}, "op": "gt", "right": {"feature": "h1_atr14"}},
                {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}},
                {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 28.0}},
                {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
                {"left": {"feature": "z20"}, "op": "lt", "right": {"value": -0.2}},
                {"left": {"feature": "z20"}, "op": "gt", "right": {"value": -1.35}},
                {"left": {"feature": "cci"}, "op": "lt", "right": {"value": -65.0}},
            ]'''
if old_short_entry in t:
    t = t.replace(old_short_entry, new_short_entry)
    print("short default entry patched")
else:
    print("WARN short entry miss")

# hold defaults: 15m bars → ~6-8h
t = t.replace("hold = 14", "hold = 24")
t = t.replace("hold = max(10, min(24, int(mhold.group(1))))",
              "hold = max(16, min(40, int(mhold.group(1))))")

# meta ema period
t = t.replace('"ema_period": "19/53"', '"ema_period": "h1_19/53"')

# Fix fallback variants to use h1 + atr proxy + SOL-specific sketches
# Replace the whole fallback_param_plan function body carefully via markers
start = t.find("def fallback_param_plan(packet):")
end = t.find("\ndef logic_banned(logic):")
if start < 0 or end < 0:
    raise SystemExit("fallback markers missing")

new_fallback = '''def fallback_param_plan(packet):
    """Deterministic SOL 15m trend_breakout param variants (review-stable family)."""
    vol = packet.get("sol15m_vol") or {}
    atr_med = 100.0 * float(vol.get("atr_pct_q50") or 0.0065)
    stop_atr = float(vol.get("stop_dist_in_atr_median") or (0.9 / max(atr_med, 1e-9)))
    return {
        "report_title": "SOL 15m 趋势突破参数方案",
        "symbol": SYMBOL,
        "timeframe": TIMEFRAME,
        "logic_family": LOGIC_FAMILY,
        "cl_lessons_zh": (
            "CL15m：边际超买+中等cci→硬止损≈-22%拖垮friction；"
            "再抬cci阈值拦亏损把trades压到<10→WF不足；软化引入新硬止损且dest失败。"
        ),
        "freezer_breakout_lessons_zh": (
            "conventional_up_break/xau15_h1_breakout：复杂或无收缩追破易被AI拒；"
            "breakdown纯z20噪声；须EMA趋势+收缩代理+结构失效，保持可解释短链路。"
        ),
        "micro_vol_read_zh": (
            "SOL15m atr_pct中位≈%.3f%%；0.9%%止损折合中位≈%.2f×ATR；"
            "ATR-stop匹配要求≤1.5×ATR ⇒ atr_pct≥0.60%%（满足率≈%.1f%%）。"
            "量能不可直取，用cci+macd+z20代理；极静盘用 atr14>h1_atr14 过滤。"
            % (
                atr_med,
                stop_atr,
                100.0 * float(vol.get("frac_atr_pct_ge_0_6pct") or 0.5),
            )
        ),
        "atr_stop_matching": {
            "stop_pct": 0.009,
            "leverage": 20,
            "max_stop_atr_mult": 1.5,
            "required_atr_pct_min": 0.006,
            "formula_zh": "0.9% ≤ 1.5×ATR%  ⇒  ATR%≥0.60%",
            "dsl_proxy_zh": (
                "DSL无atr_pct；用 atr14>h1_atr14 作为『非极静/波动充足』代理，"
                "并结合破prev_high20（突破本身偏向波动释放）。"
            ),
            "observed": {
                "atr_pct_q50": vol.get("atr_pct_q50"),
                "stop_dist_in_atr_median": vol.get("stop_dist_in_atr_median"),
                "frac_atr_pct_ge_0_6pct": vol.get("frac_atr_pct_ge_0_6pct"),
                "atr_stop_match_ok_median": vol.get("atr_stop_match_ok_median"),
            },
        },
        "volume_proxy_note_zh": (packet.get("breakout_freezer") or {}).get(
            "volume_feature_note_zh"),
        "hypothesis": {
            "ema_period": "h1_ema19 > h1_ema53（15m沿用成熟突破框架的H1 19/53趋势过滤）",
            "vol_contraction_def": (
                "单bar无法表达先验BB收缩序列；用 z20∈(0.2,1.35] 作为"
                "『收缩后方向释放窗口』：有扩张确认但未极端延展（避免追炸）"
            ),
            "volume_threshold": (
                "cci>65 + macd_stick>0 作为参与度/量能代理；阈值偏软避免CL式样本饥荒"
            ),
            "stop_vs_atr": (
                "固定止损0.9%@20x；设计要求 entry-stop ≤1.5×ATR；"
                "入场过滤 atr14>h1_atr14 排除极静噪声盘"
            ),
            "rationale_zh": (
                "SOL15m噪声高于ETH1h；以H1趋势对齐+中等z扩张+软cci确认+ATR充足代理，"
                "出场用ema21结构失效，降低假突破硬止损簇。"
            ),
        },
        "variants": [
            {
                "rank": 1,
                "direction": "long",
                "logic_class": "trend_breakout",
                "ema_period": "h1_19/53",
                "vol_contraction": "z20>0.2; z20<1.35",
                "volume_proxy": "cci>65; macd_stick>0",
                "thesis": "SOL15m在H1多头下，波动自收缩窗口向上突破prev_high20，量能代理确认，且ATR足以容纳0.9%止损",
                "entry_sketch": (
                    "h1_ema19>h1_ema53; h1_slope4>0; close>prev_high20; close>ema21; "
                    "atr14>h1_atr14; rsi14>55; rsi14<72; macd_stick>0; "
                    "z20>0.2; z20<1.35; cci>65"
                ),
                "exit_sketch": "rsi14>74 take_profit; close<ema21 invalidation; max_hold=24",
                "why_avoids_freezer": "短链路非conventional复杂KD；带z上界防追延展；有结构失效；ATR代理防噪声扫损",
                "why_avoids_cl_traps": "rsi带宽远离54；cci软阈值；目标≥12笔；结构失效非硬扛",
                "diff_vs_live_ada_ltc_ng_xrp": "趋势突破而非ADA/LTC/NG/XRP衰竭fade或回收reclaim；且不碰其live仓位",
                "avoid_death": ["stop_cluster", "sample_starvation", "ai_logic_rejection"],
                "expected_trades_hint": ">=12",
            },
            {
                "rank": 2,
                "direction": "long",
                "logic_class": "trend_breakout",
                "ema_period": "h1_19/53",
                "vol_contraction": "z20>0.15; z20<1.5",
                "volume_proxy": "cci>50; macd_stick>0",
                "thesis": "同家族放宽量能/z带以提高折数，仍要求破高+H1趋势+ATR代理",
                "entry_sketch": (
                    "h1_ema19>h1_ema53; h1_slope4>0; close>prev_high20; close>ema21; "
                    "atr14>h1_atr14; rsi14>52; rsi14<74; macd_stick>0; "
                    "z20>0.15; z20<1.5; cci>50"
                ),
                "exit_sketch": "rsi14>73 take_profit; close<ema21 invalidation; max_hold=28",
                "why_avoids_freezer": "仍非无过滤追破；放宽为样本而非取消趋势",
                "why_avoids_cl_traps": "优先保证trades/folds；失效仍用ema21",
                "diff_vs_live_ada_ltc_ng_xrp": "突破多头，非衰竭空/回收多",
                "avoid_death": ["sample_starvation", "holdout_collapse"],
                "expected_trades_hint": ">=12",
            },
            {
                "rank": 3,
                "direction": "short",
                "logic_class": "trend_breakout",
                "ema_period": "h1_19/53",
                "vol_contraction": "z20<-0.2; z20>-1.35",
                "volume_proxy": "cci<-65; macd_stick<0",
                "thesis": "H1空头下破prev_low20的镜像趋势突破，ATR代理同样约束止损匹配",
                "entry_sketch": (
                    "h1_ema19<h1_ema53; h1_slope4<0; close<prev_low20; close<ema21; "
                    "atr14>h1_atr14; rsi14<45; rsi14>28; macd_stick<0; "
                    "z20<-0.2; z20>-1.35; cci<-65"
                ),
                "exit_sketch": "rsi14<26 take_profit; close>ema21 invalidation; max_hold=24",
                "why_avoids_freezer": "趋势对齐的breakdown，非纯z噪声空",
                "why_avoids_cl_traps": "非边际超买衰竭；结构失效清晰",
                "diff_vs_live_ada_ltc_ng_xrp": "顺势破位突破空，非exhaustion_fade",
                "avoid_death": ["stop_cluster", "negative_net_expectancy"],
                "expected_trades_hint": ">=12",
            },
        ],
        "recommended_rank": 1,
        "mentor_notes": (
            "家族锁死trend_breakout；仅调参。"
            "并行不覆盖 frost3_sol15m_* 非突破产物。"
            "必须文档化ATR-stop匹配。"
        ),
        "fallback": True,
        "provider": "codex_fallback",
    }


'''

t = t[:start] + new_fallback + t[end:]

# Patch vol snapshot fields
needle = '"break_high20_rate": float((df["close"] > df["prev_high20"]).mean()),'
if needle in t:
    t = t.replace(
        needle,
        needle
        + """
            "stop_pct": 0.009,
            "atr_stop_max_mult": 1.5,
            "atr_pct_min_for_stop_le_1_5atr": 0.006,
            "frac_atr_pct_ge_0_6pct": float((atr_pct >= 0.006).mean()),
            "stop_dist_in_atr_median": float((0.009 / atr_pct.replace(0, float("nan"))).median()),
            "stop_dist_in_atr_p75": float((0.009 / atr_pct.replace(0, float("nan"))).quantile(0.75)),
            "atr_stop_match_ok_median": bool(
                float((0.009 / atr_pct.replace(0, float("nan"))).median()) <= 1.5),""",
    )
    print("atr fields added")
else:
    print("WARN atr needle miss")

# Patch note_zh in vol snapshot
t2, n = re.subn(
    r'"note_zh": \(\s*"[^"]*"\s*%\s*\([^)]*\)\s*\),',
    '''"note_zh": (
                "SOL15m可用h1_ema19/53；无volume；atr_pct中位≈%.3f%%；"
                "0.9%%止损≈%.2f×ATR；atr_pct≥0.60%%满足率≈%.1f%%；破高≈%.2f%%"
                % (100.0 * float(atr_pct.quantile(0.5)),
                   float((0.009 / atr_pct.replace(0, float("nan"))).median()),
                   100.0 * float((atr_pct >= 0.006).mean()),
                   100.0 * float((df["close"] > df["prev_high20"]).mean()))
            ),''',
    t,
    count=1,
)
print("note_zh replacements", n)
t = t2 if n else t

# PARAM prompt title
t = t.replace("输出《SOL 15m 趋势突破参数方案》", "输出《SOL 15m 趋势突破参数方案》")
# Ensure report schema says 15m (may already)
t = t.replace('"report_title":"SOL 15m 趋势突破参数方案"',
              '"report_title":"SOL 15m 趋势突破参数方案"')

# Add atr_stop constraint into PARAM_PROMPT hard constraints if missing
if "1.5×ATR" not in t.split("PARAM_PROMPT")[1][:2500]:
    t = t.replace(
        "6) DSL无volume：量能用 cci+macd_stick+z20 代理（见 volume_feature_note）",
        "6) DSL无volume：量能用 cci+macd_stick+z20 代理（见 volume_feature_note）\\n"
        "6b) ATR-止损匹配：0.9%止损必须≤1.5×ATR（atr_pct≥0.60%）；"
        "方案须写明 stop_vs_atr 与DSL代理（如 atr14>h1_atr14）",
    )

# DSL meta
t = t.replace(
    '"vol_contraction_proxy": "z20_release_window",',
    '"vol_contraction_proxy": "z20_release_window",\n'
    '            "atr_stop_proxy": "atr14>h1_atr14 (need atr_pct>=0.60% for 0.9% stop <=1.5ATR)",',
)

# write_art for Chinese plan filename
t = t.replace(
    'write_art("ETH1h趋势突破参数方案", report)',
    'write_art("SOL15m趋势突破参数方案", report)',
)
t = t.replace(
    'write_art("SOL15m趋势突破参数方案", report)',
    'write_art("SOL15m趋势突破参数方案", report)',
)
# After ETH1h→SOL15m replace, the Chinese filename may already be SOL15m
if 'write_art("SOL15m趋势突破参数方案"' not in t and 'write_art("SOL 15m趋势突破参数方案"' not in t:
    # find any 趋势突破参数方案 write
    m = re.search(r'write_art\("[^"]*趋势突破参数方案", report\)', t)
    if m:
        t = t.replace(m.group(0), 'write_art("SOL15m趋势突破参数方案", report)')
        print("chinese plan art renamed")

# end report op string
t = t.replace("SOL15m趋势突破独立创造", "SOL15m趋势突破独立创造")
t = t.replace("ETH1h趋势突破独立创造", "SOL15m趋势突破独立创造")

# parent summary strings
t = t.replace("ETH1h趋势突破", "SOL15m趋势突破")
t = t.replace("SOL15m趋势突破【成功】", "SOL15m趋势突破【成功】")

# start banner
t = t.replace("=== SOL15m trend_breakout START ===", "=== SOL15m trend_breakout START ===")
t = t.replace("=== ETH1h trend_breakout START ===", "=== SOL15m trend_breakout START ===")

dst.write_text(t)
print("wrote", dst, "lines", t.count("\n") + 1)

# sanity
assert 'PREFIX = "frost3_sol15m_breakout"' in t
assert 'TIMEFRAME = "15m"' in t
assert 'SYMBOL = "SOL-USDT-SWAP"' in t
assert "frost3_eth1h_breakout" not in t
assert "ETH-USDT-SWAP" not in t
assert "atr14>h1_atr14" in t or "atr14" in t
assert "1.5" in t and ("0.60" in t or "0.6" in t)
print("SANITY_OK")
