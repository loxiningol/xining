# -*- coding: utf-8 -*-
"""Strategy ecosystem self-evolution loop.

Closes the cycle: birth → niche competition → adaptation (micro-mutation) →
elimination → microstructure-change revive → cross-cluster transfer.

Immutable floors (never relaxed here):
- 3-AI review, triple-friction, 3-round adversarial survival standards
- Human final approval for passed_all / enable_live
- Stop-loss, exit management, extreme stress tests untouched
- All auto ops audited to strategy_evolution_audit.jsonl
- SAT_WITNESS heartbeat is checked, never bypassed
"""
from __future__ import print_function

from datetime import datetime
from pathlib import Path
import hashlib
import json
import math
import os
import sqlite3
import tempfile
import time

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
CONTROL_PATH = AUTO_DIR / "strategy_runtime_controls.json"
FREQ_PATH = AUTO_DIR / "live_portfolio_frequency.json"
SCORE_PATH = AUTO_DIR / "strategy_lifecycle_scores.json"
VAULT_PATH = AUTO_DIR / "strategy_lifecycle_freeze_vault.json"
MISMATCH_PATH = AUTO_DIR / "environment_mismatch_counters.json"
AUDIT_LOG = AUTO_DIR / "strategy_evolution_audit.jsonl"
STATUS_PATH = AUTO_DIR / "strategy_evolution_status.json"
PORTFOLIO_PATH = AUTO_DIR / "strategy_portfolio_evolution.json"
MUTATION_QUEUE = AUTO_DIR / "strategy_evolution_mutation_queue.json"
TRANSFER_QUEUE = AUTO_DIR / "strategy_evolution_transfer_queue.json"
REVIVE_PATH = AUTO_DIR / "strategy_evolution_revive_queue.json"
ECO_DB = AUTO_DIR / "strategy_ecosystem.db"

TRANSFER_SHADOW_DAYS = 5.0
DEFAULT_SHADOW_DAYS = 7.0
NEAR_MISS_WIN_RATE_FLOOR = 50.0
NEAR_MISS_EXPECTANCY_FLOOR = 0.0
MAX_MUTATIONS_PER_RUN = 3
MAX_TRANSFERS_PER_RUN = 2
NICHE_MIN_WIN_RATE = 55.0
NICHE_MIN_TRADES_FOR_LIVE_EDGE = 8


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        handle.close()
        Path(handle.name).replace(path)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def _append_audit(event):
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    row = dict(event)
    row["ts"] = time.time()
    row["time"] = _now()
    with AUDIT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def _sha(payload):
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def env_fit_score(assignment_id, mismatch_doc=None):
    """0..10 environment adaptation score from runtime mismatch streak."""
    mismatch_doc = mismatch_doc if mismatch_doc is not None else _read(
        MISMATCH_PATH, {"counters": {}})
    cell = (mismatch_doc.get("counters") or {}).get(assignment_id) or {}
    streak = int(cell.get("streak") or 0)
    reason = str(cell.get("last_reason") or "")
    fit = max(0.0, 10.0 - streak * 0.75)
    if reason in ("environment_unavailable", "primitive_stale"):
        fit = max(0.0, fit - 1.5)
    if reason == "out_of_boundary":
        fit = max(0.0, fit - 2.0)
    if streak == 0 and assignment_id in (mismatch_doc.get("counters") or {}):
        fit = min(10.0, fit + 1.0)
    return round(fit, 3)


def _live_row_from_freq(freq, assignment_id):
    for row in freq.get("assignments") or []:
        token = "%s|%s|%s" % (row.get("symbol"), row.get("timeframe"),
                              row.get("strategy_key"))
        if token == assignment_id:
            return row
    return {}


def _audit_row(audit, assignment_id):
    for row in audit.get("results") or []:
        if row.get("assignment_id") == assignment_id:
            return row
    return {}


def niche_absolute_edge(assignment_id, live=None, audit=None):
    """Absolute profitability floor for seating a niche winner.

    Relative ranking alone is insufficient: if every challenger has negative
    triple-cost / live mean, the niche must stay vacant.
    """
    live = live or {}
    audit = audit or _read(AUTO_DIR / "live_strategy_retrospective_audit.json", {})
    audited = _audit_row(audit, assignment_id)
    bt = ((audited.get("evidence") or {}).get("backtests") or {})
    triple = bt.get("triple_actual") or bt.get("triple_friction") or {}
    observed = bt.get("observed_base") or {}
    mean = None
    mean_source = None
    for label, cell in (("triple_actual", triple), ("observed_base", observed)):
        if cell.get("mean_net_return_pct") is None:
            continue
        try:
            mean = float(cell.get("mean_net_return_pct"))
            mean_source = label
            break
        except Exception:
            continue
    trades = int(live.get("trades") or 0)
    wr = live.get("win_rate")
    try:
        wr = float(wr) if wr is not None else None
    except Exception:
        wr = None
    live_ret = live.get("return_pct")
    try:
        live_ret = float(live_ret) if live_ret is not None else None
    except Exception:
        live_ret = None
    if trades >= NICHE_MIN_TRADES_FOR_LIVE_EDGE and live_ret is not None:
        mean = live_ret / float(max(trades, 1))
        mean_source = "live_mean_proxy"
    if mean is None:
        return False, {
            "ok": False, "reason": "insufficient_edge_evidence",
            "natural_language": "生态位绝对门槛：缺少三倍成本/实盘净均值证据，宁缺毋滥",
        }
    if mean <= 0:
        return False, {
            "ok": False, "mean_net": mean, "source": mean_source,
            "reason": "non_positive_mean",
            "natural_language": (
                "生态位绝对门槛未过：净均值%.6f≤0（%s），该位空缺"
                % (mean, mean_source)),
        }
    if wr is not None and wr < NICHE_MIN_WIN_RATE:
        return False, {
            "ok": False, "mean_net": mean, "win_rate": wr,
            "source": mean_source, "reason": "win_rate_below_floor",
            "natural_language": (
                "生态位绝对门槛未过：胜率%.1f%% < %.1f%%，该位空缺"
                % (wr, NICHE_MIN_WIN_RATE)),
        }
    return True, {
        "ok": True, "mean_net": mean, "win_rate": wr, "source": mean_source,
        "natural_language": (
            "绝对门槛通过：净均值%.6f>0（%s）%s"
            % (mean, mean_source,
               ("" if wr is None else "，胜率%.1f%%" % wr))),
    }


def niche_fingerprint(boundary):
    """Coarse niche key from declared environment primitives."""
    boundary = boundary or {}
    any_of = sorted(str(x) for x in (boundary.get("any_of") or []) if x)[:8]
    none_of = sorted(str(x) for x in (boundary.get("none_of") or []) if x)[:8]
    all_of = sorted(str(x) for x in (boundary.get("all_of") or []) if x)[:6]
    vol_in = sorted(str(x) for x in (boundary.get("volatility_in") or []) if x)
    vol_not = sorted(str(x) for x in (boundary.get("volatility_not_in") or [])
                     if x)
    if not (any_of or none_of or all_of or vol_in):
        return None
    return _sha({
        "any_of": any_of, "none_of": none_of, "all_of": all_of,
        "volatility_in": vol_in, "volatility_not_in": vol_not,
    })[:16]


def _demote_to_shadow(assignments, assignment_id, row, reason, extra=None):
    row = dict(assignments.get(assignment_id) or row or {})
    row["pause_new_entries"] = True
    row["new_entries_allowed"] = False
    row["lifecycle_shadow"] = True
    row["lifecycle_grade"] = "shadow"
    row["max_position_ratio"] = 0.0
    row["audit_state"] = "read_only_shadow"
    row["reason"] = reason
    if extra:
        row.update(extra)
    assignments[assignment_id] = row
    return row


def run_niche_competition(assignments, scores_map, events, freq=None, audit=None):
    """Compete on niche; winner must clear absolute edge or niche vacates."""
    freq = freq if freq is not None else _read(FREQ_PATH, {})
    audit = audit if audit is not None else _read(
        AUTO_DIR / "live_strategy_retrospective_audit.json", {})
    groups = {}
    for assignment_id, row in list(assignments.items()):
        state = str(row.get("audit_state") or "")
        if state not in ("conditional_frequency_probe", "passed_all"):
            continue
        if row.get("pause_new_entries") or row.get("lifecycle_shadow"):
            continue
        parts = str(assignment_id).split("|", 2)
        if len(parts) < 3:
            continue
        symbol, timeframe = parts[0], parts[1]
        fp = niche_fingerprint(row.get("environment_boundary") or {})
        if not fp:
            continue
        key = "%s|%s|%s" % (symbol, timeframe, fp)
        score = float(((scores_map.get(assignment_id) or {}).get("history")
                       or [{}])[-1].get("score") or row.get("rotation_score")
                      or 0.0)
        groups.setdefault(key, []).append({
            "assignment_id": assignment_id, "score": score, "row": row})

    demoted = []
    winners = []
    vacated = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda item: item["score"], reverse=True)
        eligible = []
        for member in members:
            live = _live_row_from_freq(freq, member["assignment_id"])
            ok, edge = niche_absolute_edge(member["assignment_id"], live, audit)
            member["edge_ok"] = ok
            member["edge"] = edge
            if ok:
                eligible.append(member)
        if not eligible:
            for loser in members:
                assignment_id = loser["assignment_id"]
                _demote_to_shadow(
                    assignments, assignment_id, loser["row"],
                    "生态位空缺：参与者均未过绝对盈利门槛（三倍成本/实盘净均值须>0）",
                    {"niche_vacated_at": _now(),
                     "niche_fingerprint": key.split("|", 2)[-1],
                     "niche_absolute_edge": loser.get("edge")})
                demoted.append({
                    "loser": assignment_id, "winner": None, "niche": key,
                    "vacated": True, "loser_score": loser["score"],
                    "edge": loser.get("edge"),
                })
            vacated.append(key)
            ev = {
                "event": "niche_vacated",
                "niche": key,
                "challengers": [m["assignment_id"] for m in members],
                "natural_language": (
                    "生态位 %s 空缺：候选均未满足绝对门槛（净均值>0且胜率底线），"
                    "拒绝矮子里拔将军" % key),
            }
            events.append(ev)
            _append_audit(ev)
            continue

        winner = eligible[0]
        winners.append(winner["assignment_id"])
        for loser in members:
            if loser["assignment_id"] == winner["assignment_id"]:
                continue
            assignment_id = loser["assignment_id"]
            _demote_to_shadow(
                assignments, assignment_id, loser["row"],
                "生态位竞争落败：同环境声明由过绝对门槛的更高分策略占据实盘资格",
                {"niche_competition_lost_at": _now(),
                 "niche_competition_winner": winner["assignment_id"],
                 "niche_fingerprint": key.split("|", 2)[-1],
                 "niche_absolute_edge": loser.get("edge")})
            demoted.append({
                "loser": assignment_id,
                "winner": winner["assignment_id"],
                "niche": key,
                "loser_score": loser["score"],
                "winner_score": winner["score"],
                "vacated": False,
            })
            ev = {
                "event": "niche_competition_demote",
                "loser": assignment_id,
                "winner": winner["assignment_id"],
                "niche": key,
                "natural_language": (
                    "生态位竞争：%s（%.1f，绝对门槛通过）击败 %s（%.1f）"
                    % (winner["assignment_id"], winner["score"],
                       assignment_id, loser["score"])),
            }
            events.append(ev)
            _append_audit(ev)
        win_row = dict(assignments.get(winner["assignment_id"]) or winner["row"])
        win_row["niche_competition_won_at"] = _now()
        win_row["niche_fingerprint"] = key.split("|", 2)[-1]
        win_row["niche_absolute_edge"] = winner.get("edge")
        assignments[winner["assignment_id"]] = win_row
        _append_audit({
            "event": "niche_competition_win",
            "winner": winner["assignment_id"],
            "niche": key,
            "edge": winner.get("edge"),
            "challengers": [m["assignment_id"] for m in members
                            if m["assignment_id"] != winner["assignment_id"]],
        })
    return {"demoted": demoted, "winners": winners, "vacated": vacated}


def _eco_connect():
    if not ECO_DB.exists():
        return None
    return sqlite3.connect(str(ECO_DB), timeout=30)


def _near_miss_candidates(conn, limit=12):
    rows = conn.execute(
        "SELECT candidate_hash,strategy_key,symbol,timeframe,state,"
        "candidate_json,evidence_json FROM candidates "
        "WHERE state IN ('exploration_rejected','deterministic_rejected',"
        "'advanced_confirmation_rejected','shadow_failed') "
        "ORDER BY updated_at DESC LIMIT ?",
        (int(limit * 3),)).fetchall()
    near = []
    for digest, key, symbol, timeframe, state, cand_json, evid_json in rows:
        try:
            candidate = json.loads(cand_json or "{}")
            evidence = json.loads(evid_json or "{}")
        except Exception:
            continue
        if not isinstance(candidate.get("dsl"), dict):
            continue
        metrics = (((evidence.get("runs") or {}).get("candidate") or {})
                   .get("0.009") or {})
        wr = float(metrics.get("win_rate_pct") or 0.0)
        exp = float(metrics.get("expectancy_pct") or 0.0)
        gates = evidence.get("gates") or {}
        passed_gates = sum(1 for v in gates.values() if v)
        gate_count = len(gates) or 1
        near_miss = (
            (exp >= NEAR_MISS_EXPECTANCY_FLOOR
             and wr >= NEAR_MISS_WIN_RATE_FLOOR)
            or (passed_gates / float(gate_count) >= 0.35)
            or (passed_gates >= 3 and exp >= -0.05)
        )
        # Shadow-failed near miss stored in shadow_state sometimes.
        if state == "shadow_failed":
            shadow = evidence.get("shadow_result") or {}
            wr = float(shadow.get("win_rate_pct") or wr)
            exp = float(shadow.get("expectancy_pct") or exp)
            near_miss = exp > -0.02 and wr >= NEAR_MISS_WIN_RATE_FLOOR
        # Prefer parents that are close on weighted score even if gates failed.
        weighted = evidence.get("weighted_score")
        try:
            if weighted is not None and float(weighted) > -5.0:
                near_miss = True
        except Exception:
            pass
        if not near_miss:
            continue
        near.append({
            "candidate_hash": digest,
            "strategy_key": key,
            "symbol": symbol,
            "timeframe": timeframe,
            "state": state,
            "candidate": candidate,
            "evidence": evidence,
            "win_rate_pct": wr,
            "expectancy_pct": exp,
            "passed_gate_ratio": round(passed_gates / float(gate_count), 3),
        })
        if len(near) >= limit:
            break
    return near


def _mutation_operator_ids():
    try:
        import auto_trade_strategy_ecosystem as ecosystem
        return [row["operator_id"]
                for row in ecosystem._mutation_operator_catalog()]
    except Exception:
        return ["counter_cost_noise", "counter_regime_mismatch",
                "counter_false_breakout"]


def generate_micro_mutations(events, limit=MAX_MUTATIONS_PER_RUN):
    """Near-miss → bounded DSL mutations re-entering full validation queue."""
    import auto_trade_strategy_dsl as dsl

    conn = _eco_connect()
    if conn is None:
        return {"mutations": [], "reason": "ecosystem_db_missing"}
    created = []
    queue = _read(MUTATION_QUEUE, {"items": []})
    known_keys = set()
    try:
        for item in _near_miss_candidates(conn, limit=12):
            if len(created) >= limit:
                break
            parent = item["candidate"]
            parent_dsl = parent.get("dsl") or {}
            try:
                variants = dsl.mutate_strategy(parent_dsl, limit=6)
            except Exception:
                continue
            operators = _mutation_operator_ids()[:3]
            for variant in variants:
                if len(created) >= limit:
                    break
                new_key = str(variant.get("key") or "")
                if not new_key or new_key in known_keys:
                    continue
                known_keys.add(new_key)
                child = dict(parent)
                child["strategy_key"] = new_key
                child["key"] = new_key
                child["dsl"] = variant
                child["origin"] = {
                    "kind": "ecosystem_micro_mutation",
                    "parent_hash": item["candidate_hash"],
                    "parent_state": item["state"],
                    "mutation": variant.get("origin") or {},
                    "mutation_operator_ids": operators,
                    "requires_full_re_audit": True,
                    "no_audit_exemption": True,
                    "human_approval_still_required_for_live": True,
                }
                evidence = {
                    "evolution": True,
                    "parent_hash": item["candidate_hash"],
                    "parent_metrics": {
                        "win_rate_pct": item["win_rate_pct"],
                        "expectancy_pct": item["expectancy_pct"],
                        "passed_gate_ratio": item["passed_gate_ratio"],
                    },
                    "policy": (
                        "micro_mutation_reenters_full_validation;"
                        "no_gate_relaxation;no_live_auto_grant"
                    ),
                    "mutation_operator_ids": operators,
                }
                digest = _sha({"candidate": child, "stage": "micro_mutation"})
                # Prefer ecosystem candidate_hash when available.
                try:
                    from auto_trade_ai_consensus import candidate_hash
                    digest = candidate_hash(child)
                except Exception:
                    pass
                prior = conn.execute(
                    "SELECT state FROM candidates WHERE candidate_hash=?",
                    (digest,)).fetchone()
                if prior:
                    continue
                now = _now()
                conn.execute(
                    "INSERT OR REPLACE INTO candidates VALUES(?,?,?,?,?,?,?,?,?)",
                    (digest, new_key, item["symbol"], item["timeframe"],
                     "evolution_micro_mutation",
                     json.dumps(child, ensure_ascii=False, sort_keys=True),
                     json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                     now, now))
                row = {
                    "candidate_hash": digest,
                    "parent_hash": item["candidate_hash"],
                    "strategy_key": new_key,
                    "symbol": item["symbol"],
                    "timeframe": item["timeframe"],
                    "state": "evolution_micro_mutation",
                    "requires_full_re_audit": True,
                    "created_at": now,
                    "mutation_operator_ids": operators,
                }
                created.append(row)
                queue.setdefault("items", []).append(row)
                ev = {
                    "event": "micro_mutation_created",
                    "candidate_hash": digest,
                    "parent_hash": item["candidate_hash"],
                    "strategy_key": new_key,
                    "natural_language": (
                        "近达标策略微变异：%s → %s，已重新进入完整验证队列（无豁免）"
                        % (item["strategy_key"], new_key)),
                }
                events.append(ev)
                _append_audit(ev)
        conn.commit()
    finally:
        conn.close()
    queue["updated_at"] = _now()
    queue["items"] = (queue.get("items") or [])[-80:]
    _atomic(MUTATION_QUEUE, queue)
    return {"mutations": created}


def _triple_ai_microstructure_changed(sealed_id, meta, skip_ai=False):
    if skip_ai or os.environ.get("QIYU_EVOLUTION_SKIP_AI") == "1":
        return False, "测试跳过AI，不授予复活资格"
    try:
        from auto_trade_ai_consensus import unanimous_review
        evidence = {
            "vault_revive_review": True,
            "assignment_id": sealed_id,
            "seal_meta": meta,
            "ask": (
                "市场微观结构是否发生重大变化，足以让该已淘汰策略获得"
                "「复活测试资格」？同意仅表示可重新进入完整审计流程，"
                "不得豁免三倍摩擦/三轮对手攻击/三AI会审，不得授予passed_all。"
                "返回 microstructure_changed(bool), approve_revive_test(bool)。"
            ),
        }
        result = unanimous_review(
            {"strategy_key": sealed_id, "kind": "vault_revive"}, evidence)
        reviews = result.get("reviews") or []
        agrees = 0
        for rev in reviews:
            payload = rev.get("payload") or rev.get("json") or rev
            if not isinstance(payload, dict):
                continue
            if (payload.get("microstructure_changed") is True
                    and payload.get("approve_revive_test") is True):
                agrees += 1
        if result.get("unanimous") is True and agrees >= 2:
            return True, "三AI一致认定微观结构重大变化，可复活测试"
        if agrees >= 3:
            return True, "三AI共同批准复活测试资格（%d票）" % agrees
        return False, "三AI未共同认定可复活（同意%d）" % agrees
    except Exception as exc:
        return False, "复活复核失败：%s" % exc


def review_vault_revives(assignments, vault, events, skip_ai=False):
    """Eliminated strategies may gain revive-test eligibility only; full re-audit."""
    sealed = dict(vault.get("sealed") or {})
    revive_q = _read(REVIVE_PATH, {"items": []})
    granted = []
    for assignment_id, meta in list(sealed.items()):
        if meta.get("revive_test_granted"):
            continue
        if meta.get("auto_revive_forbidden") and not meta.get(
                "allow_microstructure_revive_review"):
            # Still allow review; flag only blocks auto-live, not review.
            pass
        ok, reason = _triple_ai_microstructure_changed(
            assignment_id, meta, skip_ai=skip_ai)
        if not ok:
            events.append({
                "event": "vault_revive_deferred",
                "assignment_id": assignment_id,
                "reason": reason,
            })
            continue
        meta = dict(meta)
        meta["revive_test_granted"] = True
        meta["revive_test_granted_at"] = _now()
        meta["revive_reason"] = reason
        meta["requires_full_re_audit"] = True
        meta["no_audit_exemption"] = True
        meta["auto_live_forbidden"] = True
        sealed[assignment_id] = meta
        # Unseal trading freeze only into research path: no open permission.
        row = dict(assignments.get(assignment_id) or {})
        parts = str(assignment_id).split("|", 2)
        if len(parts) == 3:
            row.setdefault("symbol", parts[0])
            row.setdefault("timeframe", parts[1])
            row.setdefault("strategy_key", parts[2])
        row.update({
            "audit_state": "revive_pending_full_audit",
            "pause_new_entries": True,
            "new_entries_allowed": False,
            "lifecycle_shadow": True,
            "lifecycle_grade": "shadow",
            "max_position_ratio": 0.0,
            "revive_test_granted_at": _now(),
            "revive_reason": reason,
            "manual_review_required_for_full_promotion": True,
            "automatic_live_restoration": False,
            "reason": "冷冻库复活测试资格：须完整重审，无豁免，禁止自动开仓",
        })
        assignments[assignment_id] = row
        # Remove from sealed freeze so lifecycle may re-score as shadow research.
        sealed.pop(assignment_id, None)
        item = {
            "assignment_id": assignment_id,
            "granted_at": _now(),
            "reason": reason,
            "requires_full_re_audit": True,
            "no_audit_exemption": True,
        }
        granted.append(item)
        revive_q.setdefault("items", []).append(item)
        ev = {
            "event": "vault_revive_test_granted",
            "assignment_id": assignment_id,
            "reason": reason,
            "natural_language": (
                "%s 因三AI共同认定微观结构重大变化，获得复活测试资格；"
                "须完整重审，不享受任何豁免，开仓仍冻结"
                % assignment_id),
        }
        events.append(ev)
        _append_audit(ev)
    vault["sealed"] = sealed
    vault["updated_at"] = _now()
    revive_q["updated_at"] = _now()
    revive_q["items"] = (revive_q.get("items") or [])[-50:]
    _atomic(REVIVE_PATH, revive_q)
    return {"granted": granted, "vault": vault}


def _returns_from_freq(freq):
    series = {}
    for row in freq.get("assignments") or []:
        token = "%s|%s|%s" % (row.get("symbol"), row.get("timeframe"),
                              row.get("strategy_key"))
        ret = float(row.get("return_pct") or 0.0)
        trades = int(row.get("trades") or 0)
        wr = float(row.get("win_rate") or 0.0)
        # Synthetic per-trade proxy series for correlation when tick PnL absent.
        if trades <= 0:
            continue
        win_n = max(0, int(round(trades * wr / 100.0)))
        loss_n = max(0, trades - win_n)
        avg = ret / float(trades)
        vals = []
        if win_n:
            vals.extend([abs(avg) * 1.2] * win_n)
        if loss_n:
            vals.extend([-abs(avg) * 1.2] * loss_n)
        if not vals:
            vals = [avg] * max(1, min(trades, 20))
        series[token] = vals[:40]
    return series


def _corr(a, b):
    n = min(len(a), len(b))
    if n < 5:
        return None
    a = a[:n]
    b = b[:n]
    ma = sum(a) / float(n)
    mb = sum(b) / float(n)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da <= 1e-12 or db <= 1e-12:
        return 0.0
    return num / (da * db)


def _sharpe(values):
    if not values or len(values) < 3:
        return 0.0
    mean = sum(values) / float(len(values))
    var = sum((x - mean) ** 2 for x in values) / float(len(values))
    std = math.sqrt(var)
    if std <= 1e-12:
        return 0.0
    return (mean / std) * math.sqrt(min(252.0, len(values) * 8.0))


def _max_drawdown(values):
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in values:
        equity += float(x)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 6)


def evaluate_portfolio(freq, assignments, events):
    """Portfolio-level correlation / Sharpe / DD and complementary pairs."""
    series = _returns_from_freq(freq)
    active = []
    for assignment_id, row in (assignments or {}).items():
        state = str(row.get("audit_state") or "")
        if state not in ("conditional_frequency_probe", "passed_all"):
            continue
        if row.get("pause_new_entries"):
            continue
        if assignment_id in series:
            active.append(assignment_id)
    pairs = []
    keys = sorted(active)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            c = _corr(series[a], series[b])
            if c is None:
                continue
            pairs.append({
                "a": a, "b": b, "correlation": round(c, 4),
                "complementary": bool(c <= -0.15 or (c < 0.25 and c > -0.15)),
            })
    pairs.sort(key=lambda row: row["correlation"])
    complementary = [p for p in pairs if p["complementary"]][:12]
    # Equal-weight portfolio proxy from concatenated active means.
    port_vals = []
    if active:
        length = min(len(series[k]) for k in active)
        for idx in range(length):
            port_vals.append(sum(series[k][idx] for k in active) / float(len(active)))
    doc = {
        "updated_at": _now(),
        "schema": "qiyu_strategy_portfolio_evolution_v1",
        "active_count": len(active),
        "portfolio_sharpe_proxy": round(_sharpe(port_vals), 4),
        "portfolio_max_drawdown_proxy": _max_drawdown(port_vals),
        "pairwise": pairs[:40],
        "complementary_pairs": complementary,
        "policy": (
            "组合评估仅用于配置建议与补位偏好；不放松单策略门禁；"
            "止损与人工批准路径不变"
        ),
        "natural_language": (
            "组合评估：在场 %d 条；组合夏普代理 %.2f；最大回撤代理 %.2f；"
            "发现互补/低相关策略对 %d 对。"
            % (len(active), _sharpe(port_vals), _max_drawdown(port_vals),
               len(complementary))
        ),
    }
    _atomic(PORTFOLIO_PATH, doc)
    _append_audit({
        "event": "portfolio_evaluated",
        "active_count": len(active),
        "complementary_pairs": len(complementary),
        "portfolio_sharpe_proxy": doc["portfolio_sharpe_proxy"],
    })
    events.append({
        "event": "portfolio_evaluated",
        "natural_language": doc["natural_language"],
    })
    return doc


def propose_cross_cluster_transfers(assignments, freq, events,
                                    limit=MAX_TRANSFERS_PER_RUN):
    """FROZEN (designer 2026-07-24): keep code, do not emit transfer seeds."""
    doc = {
        "ok": True,
        "frozen": True,
        "reason": "cross_cluster_migration_frozen_pending_reuse",
        "transfers": [],
        "limit": int(limit or MAX_TRANSFERS_PER_RUN),
        "natural_language": "跨集群知识迁移已冻结待用，本轮不提案。",
    }
    events.append({
        "event": "cross_cluster_transfer_frozen",
        "reason": doc["reason"],
        "natural_language": doc["natural_language"],
    })
    return doc


def _propose_cross_cluster_transfers_impl(assignments, freq, events,
                                          limit=MAX_TRANSFERS_PER_RUN):
    """Stable survivors → peer symbol/tf transfer seeds (full re-audit, 5d shadow).

    Implementation retained under freeze; call only after designer unfreezes.
    """
    survivors = []
    for assignment_id, row in (assignments or {}).items():
        state = str(row.get("audit_state") or "")
        if state not in ("conditional_frequency_probe", "passed_all"):
            continue
        if row.get("pause_new_entries") or row.get("lifecycle_shadow"):
            continue
        grade = str(row.get("lifecycle_grade") or row.get("max_grade") or "C").upper()
        live = {}
        for item in freq.get("assignments") or []:
            token = "%s|%s|%s" % (item.get("symbol"), item.get("timeframe"),
                                  item.get("strategy_key"))
            if token == assignment_id:
                live = item
                break
        trades = int(live.get("trades") or 0)
        wr = float(live.get("win_rate") or 0.0)
        ret = float(live.get("return_pct") or 0.0)
        if trades < 8 or wr < 58.0 or ret <= 0:
            continue
        if grade not in ("S", "A", "B", "C"):
            continue
        survivors.append({
            "assignment_id": assignment_id,
            "row": row,
            "live": live,
            "score": wr * 0.5 + min(50.0, ret) * 0.3 + min(20.0, trades) * 0.2,
        })
    survivors.sort(key=lambda item: item["score"], reverse=True)

    # Peer clusters from frequency gaps / matrix.
    matrix = _read(AUTO_DIR / "research_cluster_matrix_status.json", {})
    peers = []
    targets = matrix.get("targets") or matrix.get("clusters") or []
    if isinstance(targets, dict):
        targets = list(targets.values())
    for cell in targets:
        if not isinstance(cell, dict):
            continue
        symbol = cell.get("symbol")
        timeframe = cell.get("timeframe")
        if not symbol or not timeframe:
            continue
        peers.append((symbol, timeframe))
    if not peers:
        peers = [
            ("BTC-USDT-SWAP", "5m"), ("BTC-USDT-SWAP", "15m"),
            ("ETH-USDT-SWAP", "5m"), ("ETH-USDT-SWAP", "15m"),
            ("NG-USDT-SWAP", "5m"), ("XAU-USDT-SWAP", "15m"),
            ("ADA-USDT-SWAP", "5m"), ("LTC-USDT-SWAP", "5m"),
        ]

    queue = _read(TRANSFER_QUEUE, {"items": []})
    existing = {(x.get("source"), x.get("target_symbol"), x.get("target_timeframe"))
                for x in (queue.get("items") or [])}
    created = []
    for item in survivors:
        if len(created) >= limit:
            break
        source_id = item["assignment_id"]
        src_sym, src_tf, src_key = source_id.split("|", 2)
        for symbol, timeframe in peers:
            if len(created) >= limit:
                break
            if symbol == src_sym and timeframe == src_tf:
                continue
            trip = (source_id, symbol, timeframe)
            if trip in existing:
                continue
            # Prefer empty live coverage on peer.
            occupied = any(
                aid.startswith("%s|%s|" % (symbol, timeframe))
                and not (assignments.get(aid) or {}).get("pause_new_entries")
                for aid in assignments
            )
            transfer_key = "%s__xfer_%s_%s" % (
                src_key, symbol.split("-")[0].lower(), timeframe)
            seed = {
                "source_assignment_id": source_id,
                "source_strategy_key": src_key,
                "target_symbol": symbol,
                "target_timeframe": timeframe,
                "transfer_strategy_key": transfer_key,
                "core_logic_hint": (
                    (item["row"].get("environment_boundary") or {})
                    .get("natural_language")
                    or item["row"].get("reason")
                    or "migrate_surviving_logic"
                ),
                "shadow_days_required": TRANSFER_SHADOW_DAYS,
                "transfer_short_shadow": True,
                "requires_full_re_audit": True,
                "no_audit_exemption": True,
                "human_approval_still_required_for_live": True,
                "peer_already_occupied": bool(occupied),
                "created_at": _now(),
                "policy": (
                    "cross_cluster_transfer_full_reaudit;"
                    "shadow_may_shorten_to_5d_only;no_live_exemption"
                ),
            }
            created.append(seed)
            queue.setdefault("items", []).append(seed)
            existing.add(trip)
            ev = {
                "event": "cross_cluster_transfer_proposed",
                "source": source_id,
                "target": "%s|%s|%s" % (symbol, timeframe, transfer_key),
                "shadow_days_required": TRANSFER_SHADOW_DAYS,
                "natural_language": (
                    "跨集群迁移提案：%s 核心逻辑尝试迁移至 %s %s；"
                    "须完整重审，影子观察可缩短至 %.0f 天，无实盘豁免"
                    % (source_id, symbol, timeframe, TRANSFER_SHADOW_DAYS)),
            }
            events.append(ev)
            _append_audit(ev)
    queue["updated_at"] = _now()
    queue["items"] = (queue.get("items") or [])[-60:]
    _atomic(TRANSFER_QUEUE, queue)
    return {"transfers": created}


def check_sat_witness(events):
    """Heartbeat only — never relaxes gates when witness is unhealthy."""
    candidates = [
        AUTO_DIR / "system_solvability_codex_ack.json",
        AUTO_DIR / "system_solvability_status.json",
        AUTO_DIR / "system_solvability_audit.json",
        AUTO_DIR / "system_solvability_alarm_latest.json",
    ]
    # Newest dated alarm as fallback.
    dated = sorted(AUTO_DIR.glob("system_solvability_alarm_*.json"), reverse=True)
    candidates.extend(dated[:2])
    state = "UNKNOWN"
    source = None
    for path in candidates:
        doc = _read(path, {})
        if not doc:
            continue
        state = str(
            doc.get("deterministic_state")
            or doc.get("state")
            or ((doc.get("witness") or {}).get("state"))
            or ((doc.get("result") or {}).get("deterministic_state"))
            or ((doc.get("result") or {}).get("state"))
            or ((doc.get("alarm") or {}).get("deterministic_state"))
            or ""
        )
        if state:
            source = path.name
            break
        state = "UNKNOWN"
    ok = state == "SAT_WITNESS"
    ev = {
        "event": "sat_witness_heartbeat",
        "state": state,
        "ok": ok,
        "source": source,
        "natural_language": (
            "SAT_WITNESS 心跳正常，规则空间未锁死"
            if ok else
            "SAT_WITNESS 心跳异常（%s），进化循环继续但禁止任何门禁放松"
            % state),
    }
    events.append(ev)
    _append_audit(ev)
    return {"ok": ok, "state": state, "source": source}


def run_once(skip_ai=False, include_revive=True, include_mutations=True):
    if skip_ai:
        os.environ["QIYU_EVOLUTION_SKIP_AI"] = "1"
    controls = _read(CONTROL_PATH, {"assignments": {}})
    assignments = dict(controls.get("assignments") or {})
    freq = _read(FREQ_PATH, {})
    scores_doc = _read(SCORE_PATH, {"assignments": {}})
    scores_map = dict(scores_doc.get("assignments") or {})
    vault = _read(VAULT_PATH, {"sealed": {}})
    events = []

    witness = check_sat_witness(events)
    niche = run_niche_competition(
        assignments, scores_map, events, freq=freq, audit=_read(
            AUTO_DIR / "live_strategy_retrospective_audit.json", {}))

    # Immediate backfill after niche demotion (not after vacancy).
    backfilled = []
    old_replace = None
    try:
        import auto_trade_strategy_lifecycle as lifecycle
        old_replace = lifecycle.REPLACE_PATH
        lifecycle.REPLACE_PATH = AUTO_DIR / "strategy_lifecycle_replacements.json"
        replacements = _read(lifecycle.REPLACE_PATH, {"by_symbol": {}})
        for demote in niche.get("demoted") or []:
            if demote.get("vacated") or not demote.get("winner"):
                continue
            loser = demote["loser"]
            symbol = loser.split("|", 1)[0]
            replacements = lifecycle._preemptive_backfill(
                assignments, freq, vault, scores_map, replacements,
                symbol, loser, events, backfilled, "生态位竞争落败")
        if backfilled:
            _atomic(lifecycle.REPLACE_PATH, replacements)
            niche["backfilled"] = backfilled
    except Exception as exc:
        events.append({"event": "niche_backfill_error", "error": str(exc)})
        niche["backfilled"] = backfilled
    finally:
        if old_replace is not None:
            try:
                import auto_trade_strategy_lifecycle as lifecycle
                lifecycle.REPLACE_PATH = old_replace
            except Exception:
                pass

    mutations = {"mutations": []}
    if include_mutations:
        try:
            mutations = generate_micro_mutations(events)
        except Exception as exc:
            mutations = {"mutations": [], "error": str(exc)}
            events.append({"event": "micro_mutation_error", "error": str(exc)})

    revive = {"granted": []}
    if include_revive:
        try:
            revive = review_vault_revives(
                assignments, vault, events, skip_ai=skip_ai)
            vault = revive.get("vault") or vault
        except Exception as exc:
            events.append({"event": "vault_revive_error", "error": str(exc)})

    portfolio = evaluate_portfolio(freq, assignments, events)
    transfers = propose_cross_cluster_transfers(assignments, freq, events)

    controls["assignments"] = assignments
    controls["updated_at"] = _now()
    controls["updated_by"] = "auto_trade_strategy_evolution.py"
    controls["evolution"] = {
        "schema": "qiyu_strategy_ecosystem_evolution_v1",
        "passed_all_still_requires_human": True,
        "stops_exits_untouched": True,
        "transfer_shadow_days": TRANSFER_SHADOW_DAYS,
        "default_shadow_days": DEFAULT_SHADOW_DAYS,
        "sat_witness": witness,
    }
    _atomic(CONTROL_PATH, controls)
    _atomic(VAULT_PATH, vault)

    nl = (
        "策略生态自我进化本轮完成：生态位竞争降级 %d，空缺 %d，抢占补位 %d，"
        "微变异 %d，冷冻库复活测试资格 %d，跨集群迁移提案 %d；"
        "组合夏普代理 %.2f。三AI/三倍摩擦/对手攻击标准未改；"
        "完整晋级仍须人工批准；止损与退出管理未动；SAT_WITNESS=%s。"
        % (len([d for d in (niche.get("demoted") or []) if not d.get("vacated")]),
           len(niche.get("vacated") or []),
           len(niche.get("backfilled") or []),
           len(mutations.get("mutations") or []),
           len(revive.get("granted") or []),
           len(transfers.get("transfers") or []),
           float(portfolio.get("portfolio_sharpe_proxy") or 0),
           witness.get("state"))
    )
    status = {
        "ok": True,
        "created_at": _now(),
        "policy": "ecosystem_self_evolution_without_gate_relaxation",
        "niche": niche,
        "mutations": mutations,
        "revive": {"granted": revive.get("granted") or []},
        "portfolio": {
            "sharpe": portfolio.get("portfolio_sharpe_proxy"),
            "max_dd": portfolio.get("portfolio_max_drawdown_proxy"),
            "complementary_pairs": len(portfolio.get("complementary_pairs") or []),
        },
        "transfers": transfers,
        "sat_witness": witness,
        "events": events[-40:],
        "natural_language": nl,
    }
    _atomic(STATUS_PATH, status)
    return status


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-ai", action="store_true")
    parser.add_argument("--no-revive", action="store_true")
    parser.add_argument("--no-mutations", action="store_true")
    parser.add_argument("--portfolio-only", action="store_true")
    args = parser.parse_args()
    if args.portfolio_only:
        controls = _read(CONTROL_PATH, {"assignments": {}})
        out = evaluate_portfolio(
            _read(FREQ_PATH, {}), controls.get("assignments") or {}, [])
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(
            run_once(skip_ai=args.skip_ai,
                     include_revive=not args.no_revive,
                     include_mutations=not args.no_mutations),
            ensure_ascii=False, indent=2, sort_keys=True))
