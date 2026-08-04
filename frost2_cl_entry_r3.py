#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜贰 CL 15m ENTRY reconstruction — Round 3 FINAL (incremental on cci>90).

If still failing gates after this round: fully archive CL 15m + 3-round history.
"""
from __future__ import print_function

import copy
import json
import os
import re
import shutil
import sys
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost2_cl_entry_r3"
ARCHIVE_DIR = os.path.join(OUT, "archive", "frost2_cl_15m_entry_exhausted_r3")


def _write(name, obj, root=OUT):
    path = os.path.join(root, name)
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def _safe_tag(tag):
    s = str(tag or "r3")
    for ch in (".", " ", "/", ":", "+", "%", "-"):
        s = s.replace(ch, "p" if ch in (".", "-") else "_")
    return s[:48]


def base_entry(cci_thr=90.0, rsi=54.0, z=0.1, extras=None):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": float(rsi)}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": float(z)}},
        {"left": {"feature": "macd_stick"}, "op": "lt", "right": {"value": 0.0}},
        {"left": {"feature": "close"}, "op": "lt", "right": {"feature": "ema16"}},
        {"left": {"feature": "cci"}, "op": "gt", "right": {"value": float(cci_thr)}},
    ]
    if extras:
        entry.extend(extras)
    return entry


def original_exit():
    return {"any": [
        {"left": {"feature": "rsi14"}, "op": "lt", "right": {"value": 45.0}, "role": "take_profit"},
        {"left": {"feature": "close"}, "op": "gt", "right": {"feature": "prev_high20"}, "role": "invalidation"},
    ]}


def make_book(cci_thr=90.0, rsi=54.0, z=0.1, extras=None, tag="r3"):
    dsl = {
        "key": "frost2_cl_entry_r3_%s" % _safe_tag(tag),
        "name": "寒霜贰-CL-15m-exhaustion_fade",
        "direction": "short",
        "entry": {"all": base_entry(cci_thr, rsi=rsi, z=z, extras=extras)},
        "exit": original_exit(),
        "max_hold_bars": 12,
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "entry reconstruction r3 incremental on cci>90",
        "title": "寒霜贰-CL-15m-exhaustion_fade",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "cl_entry_r3",
    }


GLM_PROMPT = """你是GLM-5.2。只输出JSON，禁止Markdown与省略号占位符。
Round2 结果（必须在 cci>90 栈上增量修正，禁止推倒重来）：
- 过滤：cci > 90（由 cci>100 下调）
- 两笔-22%亏损：已全部拦截 2/2
- 盈利单保留：75%（8笔中6笔），目标≥70%
- WF：6/10 未过（需≥7；实为8笔交易→仅8个非空fold，缺交易数）
- dest 过；friction Sharpe≈0.95≥0；MC未跑；未进复核
- 诊断：软化 z20 到≤0.03 可达 WF 7/10，但会引入 2026-03-26 新硬止损；加额外过滤去掉该硬止损后 logic_destruction 失败

请给出**一条**增量修正（保留 cci>90 主干）：
目标：WF≥7、盈利保留≥70%、仍拦截两笔亏损、dest过、friction≥0。
可用特征：rsi14,z20,cci,macd_stick,atr14,close,ema*,prev_high20,k,d,j,h1_*

JSON：
{
  "analysis_zh":"...",
  "patch":{
    "action":"soften_z|soften_rsi|add_entry_condition|combo",
    "cci_threshold":90.0,
    "z20_threshold":0.1,
    "rsi_threshold":54.0,
    "extra_condition":null,
    "why":"...",
    "expect_losers_blocked":2,
    "expect_winner_retention_pct":75
  },
  "dsl_extra_or_null":null
}
"""


def ask_glm(r2, pkg):
    user = {
        "round2": {
            "filter": "cci > 90",
            "losers_blocked": "2/2",
            "winner_retention_pct": 75.0,
            "wf": "6/10",
            "folds": 8,
            "dest": True,
            "friction_sharpe": 0.9546,
        },
        "diagnosis": {
            "need_trades_ge": 10,
            "soft_z_03_gets_wf7_but_new_hardstop": "2026-03-26 10:00",
            "dest_fails_when_hardstop_filtered": True,
        },
        "losers": [{
            "t": x.get("entry_time"),
            "cci": (x.get("snapshot") or {}).get("cci"),
            "rsi": (x.get("snapshot") or {}).get("rsi14"),
            "z": (x.get("snapshot") or {}).get("z20"),
            "pnl": x.get("pnl_ratio"),
        } for x in (pkg.get("losers") or [])],
        "instruction": "incremental on cci>90 only; final round",
    }
    ai = d._ai_json("glm", GLM_PROMPT, user, max_tokens=900, temperature=0.1)
    parsed = ai.get("parsed") if isinstance(ai.get("parsed"), dict) else None
    raw = ai.get("raw_preview") or ai.get("content") or ""
    if not parsed and raw:
        m = re.search(r"\{[\s\S]*\}", str(raw))
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    if parsed:
        patch = parsed.get("patch") or {}
        if str(patch.get("why", "")).strip() in ("...", ""):
            if not isinstance(patch.get("cci_threshold"), (int, float)):
                parsed = None
    _write("%s_glm.json" % PREFIX, {
        "ok": ai.get("ok"), "parsed": parsed, "error": ai.get("error"),
        "raw_preview": str(raw)[:2000],
    })
    return parsed, ai


def verify_replay(book, loser_times, winner_times):
    bt = d._backtest(book["dsl"], "CL-USDT-SWAP", "15m", "observed_base")
    trades = bt.get("trades") or []
    times = set(t.get("entry_time") for t in trades)
    still = [t for t in loser_times if t in times]
    kept = [t for t in winner_times if t in times]
    return {
        "losers_intercepted": len(loser_times) - len(still),
        "losers_still_present": still,
        "losers_total": len(loser_times),
        "winners_kept": len(kept),
        "winners_total": len(winner_times),
        "winner_retention_pct": round(100.0 * len(kept) / float(len(winner_times) or 1), 2),
        "new_trade_n": len(trades),
        "new_pnls": [float(t.get("pnl_ratio") or 0) for t in trades],
        "new_hard_stops": sum(
            1 for t in trades
            if t.get("stop_loss") or float(t.get("pnl_ratio") or 0) < -0.15),
    }


def run_gates(book):
    q = f2.quick_suite(book)
    bm = q.get("base_metrics") or {}
    out = {
        "quick": bool(q.get("quick_pass")),
        "fp": bm.get("fold_positive"),
        "folds": bm.get("folds"),
        "tr": bm.get("trades"),
        "wr": bm.get("win_rate_pct"),
        "sh": bm.get("sharpe"),
        "dest": (q.get("logic_destruction") or {}).get("pass"),
        "failed_step": q.get("failed_step"),
    }
    fr_m = ((q.get("extreme_friction") or {}).get("metrics") or {})
    out["friction_sharpe"] = fr_m.get("sharpe")
    out["friction_pass_quickpack"] = (
        int(fr_m.get("trades") or 0) >= 5
        and float(fr_m.get("sharpe") or -99) >= 0.0
    )
    if not q.get("quick_pass"):
        out["full"] = False
        return out, q
    full = f2.full_suite(book, q)
    out["full"] = bool(full.get("full_pass"))
    out["friction_sharpe"] = (full.get("full") or {}).get("friction_sharpe")
    out["friction_pass"] = bool((full.get("full") or {}).get("friction_sharpe_ge_0"))
    mc = (full.get("full") or {}).get("mc") or {}
    out["mc_beat"] = mc.get("beat_ratio")
    out["mc_actual"] = mc.get("actual_final")
    out["mc_pass"] = bool(mc.get("pass"))
    out["failed_step"] = full.get("failed_step")
    out["_packs"] = full
    return out, full


def candidate_grid(glm_parsed):
    cands = []
    if glm_parsed and isinstance(glm_parsed.get("patch"), dict):
        p = glm_parsed["patch"]
        thr = p.get("cci_threshold", 90.0)
        z = p.get("z20_threshold", 0.1)
        rsi = p.get("rsi_threshold", 54.0)
        extra = p.get("extra_condition") or glm_parsed.get("dsl_extra_or_null")
        extras = [extra] if isinstance(extra, dict) and extra.get("left") else []
        cands.append({
            "tag": "glm_cci%.0f_z%.3f_rsi%.1f" % (float(thr), float(z), float(rsi)),
            "cci": float(thr), "z": float(z), "rsi": float(rsi),
            "extras": extras, "why": p.get("why") or "glm r3", "source": "glm",
        })
    # Codex incremental on cci>90: soften z / rsi to chase WF≥7
    for z in (0.08, 0.05, 0.04, 0.03, 0.02, 0.0):
        cands.append({
            "tag": "cci90_z%.2f" % z, "cci": 90.0, "z": float(z), "rsi": 54.0,
            "extras": [], "why": "soften z under cci>90 for >=10 trades",
            "source": "codex_incremental",
        })
    for rsi in (53.5, 53.0, 52.5):
        cands.append({
            "tag": "cci90_rsi%.1f" % rsi, "cci": 90.0, "z": 0.1, "rsi": float(rsi),
            "extras": [], "why": "soften rsi under cci>90",
            "source": "codex_incremental",
        })
    for z, rsi in ((0.05, 53.5), (0.03, 54.0), (0.03, 53.5)):
        cands.append({
            "tag": "cci90_z%.2f_rsi%.1f" % (z, rsi),
            "cci": 90.0, "z": float(z), "rsi": float(rsi),
            "extras": [], "why": "combo soften z+rsi",
            "source": "codex_incremental",
        })
    # extra conditions that might block Mar26 hardstop (allowlisted only)
    for z, feat, op, val in (
        (0.03, "atr14", "lt", 0.486),
        (0.03, "j", "lt", 56.0),
        (0.03, "h1_slope4", "lt", 0.0085),
        (0.03, "k", "lt", 71.6),
        (0.03, "macd_stick", "gt", -0.063),
        (0.0, "j", "gt", 50.0),
        (0.03, "atr14", "gt", 0.32),
    ):
        cands.append({
            "tag": "cci90_z%.2f_%s_%s_%.4f" % (z, feat, op, val),
            "cci": 90.0, "z": float(z), "rsi": 54.0,
            "extras": [{
                "left": {"feature": feat}, "op": op,
                "right": {"value": float(val)},
            }],
            "why": "cci>90 soft-z plus %s %s %s to shape trades" % (feat, op, val),
            "source": "codex_incremental",
        })
    # keep R2 baseline as reference
    cands.append({
        "tag": "r2_baseline_cci90", "cci": 90.0, "z": 0.1, "rsi": 54.0,
        "extras": [], "why": "r2 baseline reference", "source": "baseline",
    })
    return cands


def apply_cand(c):
    return make_book(
        cci_thr=c.get("cci", 90.0),
        rsi=c.get("rsi", 54.0),
        z=c.get("z", 0.1),
        extras=c.get("extras") or [],
        tag=c.get("tag") or "r3",
    )


def score_row(row):
    rep = row.get("replay") or {}
    g = row.get("gates") or {}
    return (
        10000 * int(bool(g.get("full")))
        + 1000 * int(bool(g.get("quick")))
        + 200 * int(rep.get("losers_intercepted") or 0)
        + 50 * int((rep.get("winner_retention_pct") or 0) >= 70)
        + 20 * float(g.get("fp") or 0)
        + 10 * float(g.get("folds") or 0)
        + float(g.get("friction_sharpe") or -9)
        - 5 * int(rep.get("new_hard_stops") or 0)
    )


def build_history(r1, r2, interim_r3):
    return {
        "symbol": "CL-USDT-SWAP",
        "timeframe": "15m",
        "direction": "short",
        "logic": "exhaustion_fade",
        "cap_rounds": 3,
        "outcome": "ARCHIVED_AFTER_ROUND_3",
        "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "baseline_before_entry_work": {
            "entry": "rsi14>54, z20>0.1, macd_stick<0, close<ema16, cci>50",
            "exit": "rsi14<45 TP; close>prev_high20 inv; max_hold=12",
            "quick": "WF 7/10 dest pass",
            "full_fail": "friction Sharpe ≈ -0.145 (two engine hard-stops ≈-22%)",
        },
        "hard_stop_autopsy": {
            "trades": [
                {"entry": "2026-05-15 07:15", "cci": 89.73, "rsi": 54.41, "pnl": -0.2213},
                {"entry": "2026-05-26 03:15", "cci": 80.25, "rsi": 54.30, "pnl": -0.2216},
            ],
            "note": "blowups are fixed % hard stops, not slip stacking",
        },
        "rounds": [
            {
                "round": 1,
                "filter": (r1.get("glm_filter_added") or {}).get("exact_condition") or "cci > 100",
                "losers_blocked": r1.get("两笔亏损是否被拦截"),
                "winner_retention_pct": r1.get("盈利单保留比例"),
                "gates": r1.get("gates"),
                "entered_review": r1.get("entered_review"),
                "summary_zh": "上调 cci>100；亏损全拦；盈利保留62.5%<70；WF 5/10；friction≥0；未进复核",
            },
            {
                "round": 2,
                "filter": (r2.get("glm_filter_added") or {}).get("exact_condition") or "cci > 90",
                "losers_blocked": r2.get("两笔亏损是否被拦截"),
                "winner_retention_pct": r2.get("盈利单保留比例"),
                "gates": r2.get("gates"),
                "entered_review": r2.get("entered_review"),
                "summary_zh": "下调 cci>90；亏损全拦；盈利保留75%；WF 6/10（仅8笔/8fold）；dest过；friction≈0.95；未进复核",
            },
            {
                "round": 3,
                "filter": (interim_r3.get("glm_filter_added") or {}).get("exact_condition"),
                "losers_blocked": interim_r3.get("两笔亏损是否被拦截"),
                "winner_retention_pct": interim_r3.get("盈利单保留比例"),
                "gates": interim_r3.get("gates"),
                "entered_review": interim_r3.get("entered_review"),
                "diagnosis": interim_r3.get("diagnosis"),
                "summary_zh": interim_r3.get("archive_summary_zh"),
            },
        ],
        "why_exhausted": [
            "WF gate needs folds>=10 which requires >=10 trades under trade-sequence fold split",
            "cci>90 keeps 8 trades (blocks 2 losers + 2 small winners) → max fp 6/8",
            "softening z20 to <=0.03 adds trades and can reach fp 7/10 but admits Mar26 hard-stop",
            "surgical allowlisted filters that remove Mar26 also remove enough trades to lose folds>=10 or destroy dest",
            "experimental dist_ema16_atr separator reaches WF7/hard0/friction>=0 but logic_destruction fails on all 45 shape hits (seed29 turns Apr21 winner into hard-stop under ±20%)",
            "cannot restore the two cci≈76/71 winners without re-admitting losers via cci threshold alone",
        ],
        "recommendation": "Do not continue CL 15m exhaustion_fade entry reconstruction under current gates; archive niche.",
    }


def archive_cl(history, interim, search):
    if os.path.isdir(ARCHIVE_DIR):
        shutil.rmtree(ARCHIVE_DIR)
    os.makedirs(ARCHIVE_DIR)
    _write("HISTORY.json", history, root=ARCHIVE_DIR)
    _write("r3_interim.json", interim, root=ARCHIVE_DIR)
    _write("r3_search.json", search, root=ARCHIVE_DIR)
    # copy prior round artifacts
    for name in (
        "frost2_cl_entry_r1_interim.json",
        "frost2_cl_entry_r1_package.json",
        "frost2_cl_entry_r1_glm.json",
        "frost2_cl_entry_r2_interim.json",
        "frost2_cl_entry_r2_glm.json",
        "frost2_cl_entry_r2_search.json",
        "frost2_cl_entry_r3_glm.json",
        "frost2_cl_entry_r3_interim.json",
        "frost2_cl_entry_r3_search.json",
        "frost2_cl_entry_r3_dest_search.json",
        "frost2_cl_entry_r3_dest_search.log",
    ):
        src = os.path.join(OUT, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(ARCHIVE_DIR, name))
    _write("README.json", {
        "title": "CL-USDT-SWAP 15m entry reconstruction exhausted after Round 3",
        "archive_path": ARCHIVE_DIR,
        "entered_review": False,
        "see": "HISTORY.json",
    }, root=ARCHIVE_DIR)
    return ARCHIVE_DIR


def main():
    print("=== CL ENTRY R3 FINAL START ===", flush=True)
    r1 = json.load(open(os.path.join(OUT, "frost2_cl_entry_r1_interim.json")))
    r2 = json.load(open(os.path.join(OUT, "frost2_cl_entry_r2_interim.json")))
    pkg = json.load(open(os.path.join(OUT, "frost2_cl_entry_r1_package.json")))
    loser_times = [x.get("entry_time") for x in (pkg.get("losers") or [])]
    winner_times = list(pkg.get("all_winner_entry_times") or [
        w.get("entry_time") for w in (pkg.get("winners_sample") or [])])

    parsed, ai = ask_glm(r2, pkg)
    print("GLM", ai.get("ok"), ai.get("error"),
          (parsed or {}).get("patch") if parsed else None, flush=True)

    cands = candidate_grid(parsed)
    print("candidates", len(cands), flush=True)

    results = []
    winner = None
    for i, c in enumerate(cands):
        book = apply_cand(c)
        try:
            replay = verify_replay(book, loser_times, winner_times)
        except Exception as exc:
            results.append({"cand": c, "error": str(exc), "replay": None, "gates": None})
            print("ERR", c.get("tag"), exc, flush=True)
            continue
        if replay["losers_intercepted"] < 2 or replay["winner_retention_pct"] < 70:
            row = {"cand": c, "replay": replay, "gates": None, "skip": "replay_gate"}
            results.append(row)
            if i < 12 or replay["losers_intercepted"] == 2:
                print("SKIP", c["tag"], {
                    "blocked": replay["losers_intercepted"],
                    "ret": replay["winner_retention_pct"],
                    "tr": replay["new_trade_n"],
                    "hard": replay["new_hard_stops"],
                }, flush=True)
            continue
        gates, packs = run_gates(book)
        row = {
            "cand": c, "replay": replay,
            "gates": {k: gates.get(k) for k in gates if k != "_packs"},
        }
        results.append(row)
        print(
            "TRY", c["tag"],
            "tr", gates.get("tr"), "fp", gates.get("fp"), "/", gates.get("folds"),
            "dest", gates.get("dest"), "fr", gates.get("friction_sharpe"),
            "full", gates.get("full"), "hard", replay["new_hard_stops"],
            "ret", replay["winner_retention_pct"], flush=True,
        )
        if (gates.get("quick") and gates.get("full")
                and replay["losers_intercepted"] >= 2
                and replay["winner_retention_pct"] >= 70):
            winner = (book, packs, c, replay, gates)
            break

    search = {"results": results, "n": len(results)}
    _write("%s_search.json" % PREFIX, search)

    best_near = None
    for row in results:
        if not row.get("replay"):
            continue
        row["_score"] = score_row(row)
        if best_near is None or row["_score"] > best_near["_score"]:
            best_near = row

    interim = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "round": 3,
        "final_round": True,
        "built_on": "cci > 90 (round2)",
        "glm_ok": ai.get("ok"),
        "entered_review": False,
        "pending": None,
        "diagnosis": {
            "wf_needs_trades_ge": 10,
            "r2_trades": 8,
            "soft_z_path": "z<=0.03 can reach fp 7/10 but introduces Mar26 hard-stop or dest fail",
            "dest_search_shape_hits": None,
            "dest_search_dest_ok": 0,
        },
    }
    try:
        ds = json.load(open(os.path.join(OUT, "frost2_cl_entry_r3_dest_search.json")))
        interim["diagnosis"]["dest_search_shape_hits"] = len(ds.get("shape_hits") or [])
        interim["diagnosis"]["dest_search_dest_ok"] = len(ds.get("dest_ok") or [])
    except Exception:
        pass

    if winner:
        book, packs, c, replay, gates = winner
        filter_desc = {
            "exact_condition": "cci > %s; z20 > %s; rsi14 > %s" % (
                c.get("cci"), c.get("z"), c.get("rsi")),
            "cci_threshold": c.get("cci"),
            "z20_threshold": c.get("z"),
            "rsi_threshold": c.get("rsi"),
            "extras": c.get("extras"),
            "why": c.get("why"),
            "source": c.get("source"),
            "tag": c.get("tag"),
            "incremental_on": "round2 cci>90",
        }
        interim["glm_filter_added"] = filter_desc
        interim["两笔亏损是否被拦截"] = {
            "intercepted_count": replay["losers_intercepted"],
            "total": 2,
            "still_present": replay["losers_still_present"],
            "blocked_both": True,
        }
        interim["盈利单保留比例"] = replay["winner_retention_pct"]
        interim["replay"] = replay
        interim["gates"] = {
            "WF": "%s/%s pass=%s" % (gates.get("fp"), gates.get("folds"), gates.get("quick")),
            "fold_positive": gates.get("fp"),
            "folds": gates.get("folds"),
            "dest": gates.get("dest"),
            "quick_pass": gates.get("quick"),
            "MC_beat": gates.get("mc_beat"),
            "MC_pass": gates.get("mc_pass"),
            "friction_sharpe": gates.get("friction_sharpe"),
            "friction_pass": gates.get("friction_pass"),
            "full_pass": gates.get("full"),
            "failed_step": gates.get("failed_step"),
        }
        interim["dsl_entry_after"] = book["dsl"].get("entry")
        interim["dsl_exit_restored"] = book["dsl"].get("exit")
        sf = f2.run_sim_formal(book, packs)
        interim["sim"] = sf.get("sim")
        interim["formal"] = sf.get("formal")
        interim["pending"] = sf.get("pending")
        interim["sf_failed"] = sf.get("failed_step")
        interim["entered_review"] = bool((sf.get("pending") or {}).get("ok"))
        interim["archived"] = False
        _write("%s_book.json" % PREFIX, {"book": book, "filter": filter_desc})
    else:
        bn = best_near or {}
        c = (bn.get("cand") or {})
        replay = bn.get("replay") or {}
        gates = bn.get("gates") or {}
        if not gates and c:
            book = apply_cand(c)
            replay = verify_replay(book, loser_times, winner_times)
            gates, _packs = run_gates(book)
            gates = {k: gates.get(k) for k in gates if k != "_packs"}
            interim["dsl_entry_after"] = book["dsl"].get("entry")
        filter_desc = {
            "exact_condition": (
                "cci > %s; z20 > %s; rsi14 > %s" % (
                    c.get("cci"), c.get("z"), c.get("rsi"))
                if c else None
            ),
            "cci_threshold": c.get("cci"),
            "z20_threshold": c.get("z"),
            "rsi_threshold": c.get("rsi"),
            "extras": c.get("extras"),
            "why": c.get("why"),
            "source": c.get("source"),
            "tag": c.get("tag"),
            "incremental_on": "round2 cci>90",
            "note": "best_near_not_full_pass_FINAL",
        }
        interim["glm_filter_added"] = filter_desc
        interim["两笔亏损是否被拦截"] = {
            "intercepted_count": replay.get("losers_intercepted"),
            "total": 2,
            "still_present": replay.get("losers_still_present"),
            "blocked_both": replay.get("losers_intercepted") == 2,
        }
        interim["盈利单保留比例"] = replay.get("winner_retention_pct")
        interim["replay"] = replay
        interim["gates"] = {
            "WF": "%s/%s pass=%s" % (
                gates.get("fp"), gates.get("folds"), gates.get("quick")),
            "fold_positive": gates.get("fp"),
            "folds": gates.get("folds"),
            "dest": gates.get("dest"),
            "quick_pass": gates.get("quick"),
            "MC_beat": gates.get("mc_beat"),
            "MC_pass": gates.get("mc_pass"),
            "friction_sharpe": gates.get("friction_sharpe"),
            "friction_pass": gates.get("friction_pass") or gates.get("friction_pass_quickpack"),
            "full_pass": gates.get("full"),
            "failed_step": gates.get("failed_step") or "round3_no_full_winner",
        }
        interim["dsl_exit_restored"] = original_exit()
        interim["entered_review"] = False
        interim["near_misses"] = sorted(
            [r for r in results if (r.get("replay") or {}).get("losers_intercepted") == 2],
            key=score_row,
            reverse=True,
        )[:10]
        interim["archive_summary_zh"] = (
            "Round3 在 cci>90 上软化 z/rsi 或加辅助条件：可逼近 WF7，"
            "但无法同时满足 losers拦截+盈利≥70%+dest+无硬止损；按用户上限归档 CL 15m"
        )
        history = build_history(r1, r2, interim)
        arch = archive_cl(history, interim, search)
        interim["archived"] = True
        interim["archive_path"] = arch
        _write("%s_history.json" % PREFIX, history)

    _write("%s_interim.json" % PREFIX, interim)

    st_path = os.path.join(OUT, "frost2_cont_status.json")
    try:
        st = json.load(open(st_path))
    except Exception:
        st = {}
    st["cl_entry_r3"] = {
        "filter": (interim.get("glm_filter_added") or {}).get("exact_condition"),
        "losers_intercepted": (interim.get("两笔亏损是否被拦截") or {}).get("intercepted_count"),
        "winner_retention_pct": interim.get("盈利单保留比例"),
        "gates": interim.get("gates"),
        "entered_review": interim.get("entered_review"),
        "archived": interim.get("archived"),
        "archive_path": interim.get("archive_path"),
    }
    st["cl_15m_status"] = (
        "pending_review" if interim.get("entered_review")
        else ("archived_after_r3" if interim.get("archived") else "r3_failed")
    )
    open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")

    # end report update
    end = {
        "at": interim["at"],
        "task": "CL 15m entry reconstruction FINAL Round 3",
        "entered_review": interim.get("entered_review"),
        "archived": interim.get("archived"),
        "archive_path": interim.get("archive_path"),
        "interim_fields": {
            "GLM_filter": (interim.get("glm_filter_added") or {}).get("exact_condition"),
            "两笔亏损拦截": interim.get("两笔亏损是否被拦截"),
            "盈利单保留": interim.get("盈利单保留比例"),
            "gates": interim.get("gates"),
            "进入复核": interim.get("entered_review"),
        },
        "three_round_history_path": interim.get("archive_path"),
    }
    _write("frost2_cl_entry_r3_end_report.json", end)

    print("=== ROUND3 INTERIM ===", flush=True)
    print(json.dumps({
        "GLM_filter": (interim.get("glm_filter_added") or {}).get("exact_condition"),
        "两笔亏损拦截": interim.get("两笔亏损是否被拦截"),
        "盈利单保留": interim.get("盈利单保留比例"),
        "gates": interim.get("gates"),
        "进入复核": interim.get("entered_review"),
        "archived": interim.get("archived"),
        "archive_path": interim.get("archive_path"),
    }, ensure_ascii=False, indent=2), flush=True)
    if interim.get("archived"):
        print("ARCHIVED CL 15m →", interim.get("archive_path"), flush=True)
    return 0 if interim.get("entered_review") else 1


if __name__ == "__main__":
    sys.exit(main())
