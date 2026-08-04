#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""寒霜贰 CL 15m ENTRY reconstruction — Round 2 (incremental on cci>100)."""
from __future__ import print_function

import copy
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, "/root")
import frost2_action_run as f2
import auto_trade_dual_engine_factory as d

f2.QUICK_WF_POS = 7
d.FROST_RELAXED_WF_POS = 7
OUT = "/root/auto_trade/dual_engine"
PREFIX = "frost2_cl_entry_r2"


def _write(name, obj):
    path = os.path.join(OUT, name)
    open(path, "w").write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def base_entry(cci_thr=100.0, extras=None):
    entry = [
        {"left": {"feature": "rsi14"}, "op": "gt", "right": {"value": 54.0}},
        {"left": {"feature": "z20"}, "op": "gt", "right": {"value": 0.1}},
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


def _safe_tag(tag):
    s = str(tag or "r2")
    for ch in (".", " ", "/", ":", "+", "%"):
        s = s.replace(ch, "p" if ch == "." else "_")
    return s[:48]


def make_book(cci_thr=100.0, extras=None, tag="r2"):
    dsl = {
        "key": "frost2_cl_entry_r2_%s" % _safe_tag(tag),
        "name": "寒霜贰-CL-15m-exhaustion_fade",
        "direction": "short",
        "entry": {"all": base_entry(cci_thr, extras)},
        "exit": original_exit(),
        "max_hold_bars": 12,
    }
    return {
        "symbol": "CL-USDT-SWAP", "timeframe": "15m", "direction": "short",
        "logic_class": "exhaustion_fade",
        "thesis": "entry reconstruction r2 incremental",
        "title": "寒霜贰-CL-15m-exhaustion_fade",
        "dsl": f2.ensure_dsl(dsl, "CL-USDT-SWAP", "15m"),
        "gate_mode": "frost2", "source": "cl_entry_r2",
    }


GLM_PROMPT = """你是GLM-5.2。只输出JSON，禁止Markdown与省略号占位符。
Round1 结果（必须在此基础上增量修正，禁止推倒重来）：
- 已加过滤：cci > 100（由 cci>50 上调）
- 两笔-22%亏损：已全部拦截 2/2
- 盈利单保留：62.5%（8笔中5笔），目标≥70%
- WF：5/10 未过（需≥7）；dest 过；friction Sharpe=0.6638 已≥0；MC未跑
- 亏损单CCI约 89.7 / 80.3；被误杀盈利单CCI约 97 / 76 / 71

请给出**一条**增量修正（优先：下调cci阈值但仍>两笔亏损CCI，或在保留cci阈值前提下加一条可DSL特征条件）：
目标：WF≥7、盈利保留≥70%、仍拦截两笔亏损、friction≥0。

JSON：
{
  "analysis_zh":"...",
  "patch":{
    "action":"soften_cci_threshold|add_entry_condition|both",
    "cci_threshold":90.0,
    "extra_condition":null,
    "why":"...",
    "expect_losers_blocked":2,
    "expect_winner_retention_pct":75
  },
  "dsl_extra_or_null":null
}
extra_condition 若有则形如 {"left":{"feature":"z20"},"op":"gt","right":{"value":0.15}}
可用特征：rsi14,z20,cci,macd_stick,atr14,close,ema*,prev_high20,k,d,j,h1_*
"""


def ask_glm(r1, pkg):
    user = {
        "round1": {
            "filter": "cci > 100",
            "losers_blocked": "2/2",
            "winner_retention_pct": 62.5,
            "wf": "5/10",
            "dest": True,
            "friction_sharpe": 0.6638,
        },
        "losers": [{
            "t": x.get("entry_time"),
            "cci": (x.get("snapshot") or {}).get("cci"),
            "rsi": (x.get("snapshot") or {}).get("rsi14"),
            "z": (x.get("snapshot") or {}).get("z20"),
            "pnl": x.get("pnl_ratio"),
        } for x in (pkg.get("losers") or [])],
        "winners": [{
            "t": x.get("entry_time"),
            "cci": (x.get("snapshot") or {}).get("cci"),
            "rsi": (x.get("snapshot") or {}).get("rsi14"),
            "z": (x.get("snapshot") or {}).get("z20"),
            "pnl": x.get("pnl_ratio"),
            "kept_under_cci100": float((x.get("snapshot") or {}).get("cci") or 0) > 100,
        } for x in (pkg.get("winners_sample") or [])],
        "instruction": "incremental on cci>100 only",
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
    # reject ellipsis placeholders
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
    """Incremental candidates on top of R1 cci>100."""
    cands = []
    # GLM suggestion first
    if glm_parsed and isinstance(glm_parsed.get("patch"), dict):
        p = glm_parsed["patch"]
        thr = p.get("cci_threshold")
        extra = p.get("extra_condition") or glm_parsed.get("dsl_extra_or_null")
        if isinstance(thr, (int, float)):
            cands.append({
                "tag": "glm_cci_%.1f" % float(thr),
                "cci": float(thr),
                "extras": [extra] if isinstance(extra, dict) and extra.get("left") else [],
                "why": p.get("why") or "glm incremental",
                "source": "glm",
            })
    # Soften cci threshold: must stay above max loser cci (~89.73)
    for thr in (89.8, 90.0, 92.0, 95.0, 97.0, 98.0):
        cands.append({
            "tag": "soften_cci_%.1f" % thr,
            "cci": float(thr),
            "extras": [],
            "why": "soften R1 cci>100 to >%.1f; still above loser CCI max 89.73" % thr,
            "source": "codex_incremental",
        })
    # soften + light z floor (loser1 z=0.119 — careful)
    for thr, zlo in ((90.0, 0.12), (90.0, 0.15), (92.0, 0.12), (95.0, 0.11)):
        cands.append({
            "tag": "cci_%.0f_z_%.2f" % (thr, zlo),
            "cci": float(thr),
            "extras": [{
                "left": {"feature": "z20"}, "op": "gt",
                "right": {"value": float(zlo)},
            }],
            "why": "cci>%.0f plus z20>%.2f incremental" % (thr, zlo),
            "source": "codex_incremental",
        })
    # soften + rsi slight raise (losers <54.42)
    for thr, rsi in ((90.0, 54.2), (90.0, 54.5), (92.0, 54.3), (95.0, 54.5)):
        cands.append({
            "tag": "cci_%.0f_rsi_%.1f" % (thr, rsi),
            "cci": float(thr),
            "extras": [],  # rsi applied by replacing base rsi threshold
            "rsi": float(rsi),
            "why": "cci>%.0f and raise rsi>%.1f" % (thr, rsi),
            "source": "codex_incremental",
        })
    return cands


def apply_cand(c):
    extras = list(c.get("extras") or [])
    book = make_book(cci_thr=c["cci"], extras=extras, tag=c["tag"])
    if c.get("rsi") is not None:
        for row in book["dsl"]["entry"]["all"]:
            if (row.get("left") or {}).get("feature") == "rsi14":
                row["right"] = {"value": float(c["rsi"])}
                break
        # if also z extra as replace
    # if extras contain z20 gt, replace existing z20 condition instead of duplicate
    new_all = []
    z_extra = None
    other_extras = []
    for e in extras:
        if (e.get("left") or {}).get("feature") == "z20":
            z_extra = e
        else:
            other_extras.append(e)
    for row in book["dsl"]["entry"]["all"]:
        feat = (row.get("left") or {}).get("feature")
        if feat == "z20" and z_extra is not None:
            row = copy.deepcopy(z_extra)
        new_all.append(row)
    for e in other_extras:
        new_all.append(copy.deepcopy(e))
    book["dsl"]["entry"]["all"] = new_all
    book["dsl"]["key"] = "frost2_cl_entry_r2_%s" % _safe_tag(c.get("tag"))
    book["dsl"] = f2.ensure_dsl(book["dsl"], "CL-USDT-SWAP", "15m")
    return book


def main():
    print("=== CL ENTRY R2 START ===", flush=True)
    r1 = json.load(open(os.path.join(OUT, "frost2_cl_entry_r1_interim.json")))
    pkg = json.load(open(os.path.join(OUT, "frost2_cl_entry_r1_package.json")))
    loser_times = [x.get("entry_time") for x in (pkg.get("losers") or [])]
    winner_times = list(pkg.get("all_winner_entry_times") or [
        w.get("entry_time") for w in (pkg.get("winners_sample") or [])])

    parsed, ai = ask_glm(r1, pkg)
    print("GLM", ai.get("ok"), ai.get("error"),
          (parsed or {}).get("patch") if parsed else None, flush=True)

    cands = candidate_grid(parsed)
    print("candidates", len(cands), flush=True)

    results = []
    winner = None
    for i, c in enumerate(cands):
        book = apply_cand(c)
        replay = verify_replay(book, loser_times, winner_times)
        # skip early if losers not both blocked or retention < 70
        if replay["losers_intercepted"] < 2 or replay["winner_retention_pct"] < 70:
            row = {
                "cand": c, "replay": replay, "gates": None,
                "skip": "replay_gate",
            }
            results.append(row)
            if i < 8 or replay["losers_intercepted"] == 2:
                print("SKIP", c["tag"], replay, flush=True)
            continue
        gates, packs = run_gates(book)
        row = {
            "cand": c, "replay": replay,
            "gates": {k: gates.get(k) for k in gates if k != "_packs"},
        }
        results.append(row)
        print("TRY", c["tag"], "fp", gates.get("fp"), "dest", gates.get("dest"),
              "fr", gates.get("friction_sharpe"), "full", gates.get("full"),
              "ret", replay["winner_retention_pct"], flush=True)
        if (gates.get("quick") and gates.get("full")
                and replay["losers_intercepted"] >= 2
                and replay["winner_retention_pct"] >= 70):
            winner = (book, packs, c, replay, gates)
            break
        # also track best near: quick pass + losers blocked + ret>=70
        if gates.get("quick") and not winner:
            # keep searching for full
            pass

    _write("%s_search.json" % PREFIX, {"results": results[:40], "n": len(results)})

    # pick best near if no full winner: maximize (fp, friction, retention) among loser-blocked
    best_near = None
    for row in results:
        rep = row.get("replay") or {}
        g = row.get("gates") or {}
        if rep.get("losers_intercepted", 0) < 2:
            continue
        if rep.get("winner_retention_pct", 0) < 70 and not g.get("quick"):
            # still consider for report if better retention
            pass
        score = (
            10000 * int(bool(g.get("full")))
            + 1000 * int(bool(g.get("quick")))
            + 100 * int(rep.get("losers_intercepted") or 0)
            + 10 * float(rep.get("winner_retention_pct") or 0)
            + float(g.get("fp") or 0)
            + float(g.get("friction_sharpe") or -9)
        )
        row["_score"] = score
        if best_near is None or score > best_near["_score"]:
            best_near = row

    interim = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "round": 2,
        "stop_after_round": True,
        "built_on": "cci > 100 (round1)",
        "glm_ok": ai.get("ok"),
        "entered_review": False,
        "pending": None,
    }

    if winner:
        book, packs, c, replay, gates = winner
        filter_desc = {
            "exact_condition": "cci > %s" % c["cci"],
            "cci_threshold": c["cci"],
            "extras": c.get("extras"),
            "rsi": c.get("rsi"),
            "why": c.get("why"),
            "source": c.get("source"),
            "tag": c.get("tag"),
            "incremental_on": "round1 cci>100",
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
            "WF": "%s/10 pass=%s" % (gates.get("fp"), gates.get("quick")),
            "fold_positive": gates.get("fp"),
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
        _write("%s_book.json" % PREFIX, {"book": book, "filter": filter_desc})
        print("SF", interim.get("sf_failed"), interim.get("pending"), flush=True)
        if interim["entered_review"]:
            st_path = os.path.join(OUT, "frost2_cont_status.json")
            try:
                st = json.load(open(st_path))
            except Exception:
                st = {}
            keys = list(st.get("pending_keys") or [])
            k = (sf.get("pending") or {}).get("key")
            if k and k not in keys:
                keys.append(k)
            st["pending_keys"] = keys
            st["ok_min_cl"] = True
            st["cl_entry_r2"] = {
                "filter": filter_desc.get("exact_condition"),
                "entered_review": True,
                "pending": k,
            }
            open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")
    else:
        # report best near
        bn = best_near or {}
        c = (bn.get("cand") or {})
        replay = bn.get("replay") or {}
        gates = bn.get("gates") or {}
        # if best near had no gates (skipped), still run gates once for report
        if not gates and c:
            book = apply_cand(c)
            replay = verify_replay(book, loser_times, winner_times)
            gates, _packs = run_gates(book)
            gates = {k: gates.get(k) for k in gates if k != "_packs"}
            interim["dsl_entry_after"] = book["dsl"].get("entry")
        filter_desc = {
            "exact_condition": ("cci > %s" % c.get("cci")) if c else None,
            "cci_threshold": c.get("cci"),
            "extras": c.get("extras"),
            "rsi": c.get("rsi"),
            "why": c.get("why"),
            "source": c.get("source"),
            "tag": c.get("tag"),
            "incremental_on": "round1 cci>100",
            "note": "best_near_not_full_pass",
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
            "WF": "%s/10 pass=%s" % (gates.get("fp"), gates.get("quick")),
            "fold_positive": gates.get("fp"),
            "dest": gates.get("dest"),
            "quick_pass": gates.get("quick"),
            "MC_beat": gates.get("mc_beat"),
            "MC_pass": gates.get("mc_pass"),
            "friction_sharpe": gates.get("friction_sharpe"),
            "friction_pass": gates.get("friction_pass") or gates.get("friction_pass_quickpack"),
            "full_pass": gates.get("full"),
            "failed_step": gates.get("failed_step") or "round2_no_full_winner",
        }
        interim["dsl_exit_restored"] = original_exit()
        interim["entered_review"] = False
        # top scored with ret>=70 and losers 2 for transparency
        interim["near_misses"] = sorted(
            [r for r in results if (r.get("replay") or {}).get("losers_intercepted") == 2],
            key=lambda r: (
                int(bool((r.get("gates") or {}).get("full"))),
                int(bool((r.get("gates") or {}).get("quick"))),
                float((r.get("replay") or {}).get("winner_retention_pct") or 0),
                float((r.get("gates") or {}).get("fp") or 0),
            ),
            reverse=True,
        )[:8]

    _write("%s_interim.json" % PREFIX, interim)
    st_path = os.path.join(OUT, "frost2_cont_status.json")
    try:
        st = json.load(open(st_path))
    except Exception:
        st = {}
    st["cl_entry_r2"] = {
        "filter": (interim.get("glm_filter_added") or {}).get("exact_condition"),
        "losers_intercepted": (interim.get("两笔亏损是否被拦截") or {}).get("intercepted_count"),
        "winner_retention_pct": interim.get("盈利单保留比例"),
        "gates": interim.get("gates"),
        "entered_review": interim.get("entered_review"),
    }
    open(st_path, "w").write(json.dumps(st, ensure_ascii=False, indent=2) + "\n")

    print("=== ROUND2 INTERIM ===", flush=True)
    print(json.dumps({
        "GLM_filter": (interim.get("glm_filter_added") or {}).get("exact_condition"),
        "两笔亏损拦截": interim.get("两笔亏损是否被拦截"),
        "盈利单保留": interim.get("盈利单保留比例"),
        "gates": interim.get("gates"),
        "进入复核": interim.get("entered_review"),
    }, ensure_ascii=False, indent=2), flush=True)
    print("STOP after Round 2", flush=True)
    return 0 if interim.get("entered_review") else 1


if __name__ == "__main__":
    sys.exit(main())
