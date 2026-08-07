# -*- coding: utf-8 -*-
"""Candidate materialization contract — create first, judge later.

Empty manufacture batches are PIPELINE FAILURES, not market research conclusions.
Language models must not terminate a direction with \"no credible candidate\".
"""
from __future__ import print_function

from datetime import datetime

# Minimum yield (plan §5 / §11). Floor raised so skeleton injection has room
# to diagnose compile-vs-edge failures; MMVQ E/WR/R gates stay untouched.
MIN_COMPILED_CANDIDATES = 60
TARGET_COMPILED_CANDIDATES = 90
MAX_REPAIR_ROUNDS = 3  # Wave1 direct + Wave2 state + Wave3 combo

# Failure taxonomy (plan §6)
FAIL_HYPOTHESIS = "hypothesis_failure"          # A: compiled + probed, stats dead
FAIL_REPRESENTATION = "representation_failure"  # B: mechanism unexpressable
FAIL_SEARCH = "search_failure"                  # C: too few / collapsed diversity
FAIL_COMPILE = "compile_failure"                # D: JSON/DSL won't compile
FAIL_MATERIALIZATION = "CANDIDATE_MATERIALIZATION_FAILURE"

WAVE_DIRECT = "wave1_direct_mechanism"
WAVE_STATE = "wave2_state_conditioned"
WAVE_COMBO = "wave3_combo_nonlinear"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def classify_empty_batch(metrics):
    """Classify why the candidate pool is empty."""
    m = metrics or {}
    n_hyp = int(m.get("hypothesis_count") or 0)
    n_repr = int(m.get("representation_count") or n_hyp)
    n_compile_ok = int(m.get("compile_success_count") or 0)
    n_compile_attempt = int(m.get("compile_attempt_count") or 0)
    n_probe = int(m.get("probe_count") or 0)
    n_survivors = int(m.get("survivor_count") or 0)
    n_near = int(m.get("near_miss_count") or 0)

    codes = []
    if n_repr < 8:
        codes.append(FAIL_SEARCH)
    # Upstream historically aliased survivors as compile_success_count. When
    # probes actually ran, survivors=0 is an edge/AF kill — not a dead compiler.
    if n_probe > 0 and n_survivors == 0:
        codes.append(FAIL_HYPOTHESIS)
    elif n_compile_attempt > 0 and n_compile_ok == 0 and n_probe == 0:
        codes.append(FAIL_COMPILE)
    if n_compile_ok > 0 and n_survivors == 0 and n_near == 0 and n_probe > 0:
        if FAIL_HYPOTHESIS not in codes:
            codes.append(FAIL_HYPOTHESIS)
    if n_repr >= 8 and n_compile_ok < MIN_COMPILED_CANDIDATES and n_probe == 0:
        codes.append(FAIL_REPRESENTATION)
    if not codes:
        codes.append(FAIL_MATERIALIZATION)
    primary = codes[0]
    if n_probe == 0 and n_compile_ok < MIN_COMPILED_CANDIDATES:
        primary = FAIL_MATERIALIZATION
    if n_probe > 0 and n_survivors == 0:
        message_zh = (
            "管道生成失败：探针后幸存者=0（survivors=%d / probes=%d / hyp=%d）；"
            "常见根因是 lean 地图空转或期望为负被 AF 硬杀——不得伪装成「市场无可信策略」，"
            "也不得误报为编译器宕机。"
            % (n_survivors, n_probe, n_hyp)
        )
        primary = FAIL_HYPOTHESIS
    else:
        message_zh = (
            "管道生成失败：候选材料化不足（compiled=%d < min=%d），"
            "不得伪装成「市场无可信策略」。"
            % (n_compile_ok, MIN_COMPILED_CANDIDATES)
        )
    return {
        "primary": primary,
        "codes": codes,
        "is_pipeline_error": True,
        "is_market_research_rejection": False,
        "message_zh": message_zh,
        "metrics": {
            "hypothesis_count": n_hyp,
            "representation_count": n_repr,
            "compile_attempt_count": n_compile_attempt,
            "compile_success_count": n_compile_ok,
            "probe_count": n_probe,
            "survivor_count": n_survivors,
            "near_miss_count": n_near,
            "materialization_rate": (
                (float(n_compile_ok) / float(n_repr)) if n_repr else 0.0
            ),
        },
    }


def materialization_report(
    hypothesis_count=0,
    representation_count=0,
    compile_attempt_count=0,
    compile_success_count=0,
    probe_count=0,
    survivor_count=0,
    near_miss_count=0,
    repair_round_count=0,
    waves=None,
    termination_reason=None,
):
    metrics = {
        "hypothesis_count": int(hypothesis_count or 0),
        "representation_count": int(representation_count or 0),
        "compile_attempt_count": int(compile_attempt_count or 0),
        "compile_success_count": int(compile_success_count or 0),
        "probe_count": int(probe_count or 0),
        "survivor_count": int(survivor_count or 0),
        "near_miss_count": int(near_miss_count or 0),
        "repair_round_count": int(repair_round_count or 0),
        "waves": list(waves or []),
        "termination_reason": termination_reason,
    }
    n_repr = metrics["representation_count"] or metrics["hypothesis_count"]
    n_comp = metrics["compile_success_count"]
    metrics["materialization_rate"] = (
        float(n_comp) / float(n_repr) if n_repr else 0.0
    )
    metrics["meets_minimum"] = n_comp >= MIN_COMPILED_CANDIDATES
    metrics["meets_target"] = n_comp >= TARGET_COMPILED_CANDIDATES
    if n_comp < MIN_COMPILED_CANDIDATES:
        metrics["failure"] = classify_empty_batch(metrics)
    else:
        metrics["failure"] = None
    metrics["at"] = _now()
    return metrics


def skeleton_templates_for_family(family, direction="long"):
    """Forced strategy skeletons (plan §4 / §9) — structure before evidence."""
    side = str(direction or "long").lower()
    fam = str(family or "exhaustion").lower()
    base = []
    if any(x in fam for x in ("exhaust", "衰竭", "mean_reversion", "liquid")):
        base = [
            {
                "family_variant": "急跌+卖压衰减+结构收复",
                "event": "downside_impulse",
                "state_before": "trend_or_range_neutral",
                "trigger": ["rsi_14_low", "close_z_20_low"],
                "confirmation": ["bullish_reclaim_high", "downside_velocity_decay_high"],
                "exclusion": ["expansion_score_extreme"],
                "expected_path": "profit_before_stop_0p5555",
                "failure_mode": "falling_knife_continuation",
                "wave": WAVE_DIRECT,
            },
            {
                "family_variant": "急跌+下影+布林下轨回收",
                "event": "bb_lower_touch",
                "state_before": "volatility_not_panic",
                "trigger": ["bb_lower_dist_low", "rsi_14_low"],
                "confirmation": ["bb_mid_reclaim_high", "lower_wick_pct_high"],
                "exclusion": ["bb_width_extreme_high"],
                "expected_path": "profit_before_stop_0p5555",
                "failure_mode": "bandwidth_expansion_breakdown",
                "wave": WAVE_STATE,
            },
            {
                "family_variant": "急跌+成交量衰减+收复中位",
                "event": "sell_pressure_decay",
                "state_before": "volume_spike_then_fade",
                "trigger": ["volume_z_high_then_delta_neg", "close_z_20_low"],
                "confirmation": ["close_location_high", "reclaim_strength_high"],
                "exclusion": ["volatility_acceleration_high"],
                "expected_path": "profit_before_stop_0p5555",
                "failure_mode": "second_leg_down",
                "wave": WAVE_STATE,
            },
            {
                "family_variant": "压缩后假跌破回收",
                "event": "squeeze_fake_break",
                "state_before": "squeeze_persistence_high",
                "trigger": ["close_z_20_low", "squeeze_persistence_high"],
                "confirmation": ["bullish_reclaim_high", "expansion_score_moderate"],
                "exclusion": ["trend_bias_strong_down"],
                "expected_path": "profit_before_stop_0p5555",
                "failure_mode": "true_break_continuation",
                "wave": WAVE_COMBO,
            },
            {
                "family_variant": "RSI超卖+变化率改善",
                "event": "rsi_turn",
                "state_before": "oversold",
                "trigger": ["rsi_14_low", "delta_rsi_up"],
                "confirmation": ["ret_1_nonneg", "close_location_high"],
                "exclusion": ["ret_12_strong_down"],
                "expected_path": "profit_before_stop_0p5555",
                "failure_mode": "oversold_trend_relay",
                "wave": WAVE_COMBO,
            },
            {
                "family_variant": "布林带宽压缩后下轨触碰",
                "event": "bb_squeeze_touch",
                "state_before": "bb_width_low",
                "trigger": ["bb_width_low", "bb_lower_dist_low"],
                "confirmation": ["bb_mid_reclaim_high", "rsi_14_rising"],
                "exclusion": ["volume_z_collapse"],
                "expected_path": "profit_before_stop_0p5555",
                "failure_mode": "squeeze_breakdown",
                "wave": WAVE_COMBO,
            },
        ]
    elif any(x in fam for x in ("break", "donchian", "trend", "squeeze")):
        base = [
            {
                "family_variant": "压缩后突破确认",
                "event": "squeeze_break",
                "state_before": "squeeze_persistence_high",
                "trigger": ["expansion_score_high", "donchian20_long_break"],
                "confirmation": ["breakout_acceptance_high", "volume_z_high"],
                "exclusion": ["fake_break_reversion"],
                "expected_path": "profit_before_stop_0p5555",
                "failure_mode": "failed_breakout",
                "wave": WAVE_DIRECT,
            },
        ] * 6
    else:
        base = [
            {
                "family_variant": "generic_impulse_reclaim",
                "event": "impulse",
                "state_before": "neutral",
                "trigger": ["close_z_20_extreme", "volume_z_high"],
                "confirmation": ["reclaim_strength_high"],
                "exclusion": ["volatility_acceleration_high"],
                "expected_path": "profit_before_stop_0p5555",
                "failure_mode": "noise",
                "wave": WAVE_DIRECT,
            },
        ] * 6
    out = []
    for index, row in enumerate(base[:6]):
        item = dict(row)
        item["direction"] = side
        item["skeleton_id"] = "sk_%s_%s_%d" % (fam[:12], side, index + 1)
        item["max_conditions"] = 4
        out.append(item)
    return out


def expand_factor_hints(failure_codes=None, base_hints=None, wave=WAVE_STATE):
    """Representation expansion menus for repair waves."""
    hints = list(base_hints or [])
    wave = str(wave or WAVE_STATE)
    if wave == WAVE_STATE:
        hints.extend([
            "rsi_14", "bb_lower_dist", "bb_mid_reclaim", "bb_width",
            "squeeze_persistence", "trend_bias_50_200", "volume_z",
            "close_location", "reclaim_strength", "bullish_reclaim",
        ])
    elif wave == WAVE_COMBO:
        hints.extend([
            "downside_velocity_decay", "upside_velocity_decay",
            "exhaustion_score", "absorption_proxy", "impact_decay_proxy",
            "volatility_acceleration", "trend_efficiency_12",
            "signed_volume_pressure", "ret_3", "ret_12",
        ])
    else:
        hints.extend(["rsi_14", "close_z_20", "volume_z", "bb_lower_dist"])
    # de-dupe preserve order
    seen = set()
    out = []
    for name in hints:
        if name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def forced_probe_event_defs(wave=WAVE_STATE):
    """Extra mechanism-preserving event definitions for repair waves."""
    wave = str(wave or WAVE_STATE)
    if wave == WAVE_DIRECT:
        return (
            (
                "mat_rsi_z_reclaim",
                (("rsi_14", "low", 0.75),
                 ("close_z_20", "low", 0.75),
                 ("bullish_reclaim", "high", 0.70)),
            ),
            (
                "mat_bb_rsi_core",
                (("rsi_14", "low", 0.75),
                 ("bb_lower_dist", "low", 0.75),
                 ("bb_mid_reclaim", "high", 0.65)),
            ),
        )
    if wave == WAVE_STATE:
        return (
            (
                "mat_bb_width_guard_state",
                (("rsi_14", "low", 0.70),
                 ("bb_lower_dist", "low", 0.70),
                 ("bb_mid_reclaim", "high", 0.65),
                 ("bb_width", "low", 0.70)),
            ),
            (
                "mat_squeeze_reclaim_state",
                (("squeeze_persistence", "high", 0.75),
                 ("close_z_20", "low", 0.70),
                 ("bullish_reclaim", "high", 0.70)),
            ),
            (
                "mat_volume_decay_state",
                (("volume_z", "high", 0.70),
                 ("downside_velocity_decay", "high", 0.70),
                 ("reclaim_strength", "high", 0.65)),
            ),
            (
                "mat_trend_filter_exhaust",
                (("rsi_14", "low", 0.75),
                 ("close_z_20", "low", 0.75),
                 ("trend_bias_50_200", "high", 0.55),
                 ("bullish_reclaim", "high", 0.70)),
            ),
        )
    # WAVE_COMBO
    return (
        (
            "mat_combo_exhaust_absorb",
            (("exhaustion_score", "high", 0.75),
             ("absorption_proxy", "high", 0.70),
             ("bullish_reclaim", "high", 0.70)),
        ),
        (
            "mat_combo_bb_velocity",
            (("bb_lower_dist", "low", 0.70),
             ("downside_velocity_decay", "high", 0.70),
             ("bb_mid_reclaim", "high", 0.65),
             ("rsi_14", "low", 0.70)),
        ),
        (
            "mat_combo_eff_reclaim",
            (("trend_efficiency_12", "low", 0.70),
             ("close_z_20", "low", 0.70),
             ("reclaim_strength", "high", 0.70),
             ("volume_z", "high", 0.60)),
        ),
        (
            "mat_combo_impact_decay",
            (("impact_decay_proxy", "high", 0.70),
             ("signed_volume_pressure", "low", 0.70),
             ("bullish_reclaim", "high", 0.70)),
        ),
    )


def skeleton_to_hypothesis(skeleton, family="exhaustion", index=0, wave=None,
                           representation_type=None, event_ast=None):
    """Turn a forced skeleton into a probeable hypothesis row."""
    sk = dict(skeleton or {})
    wave = str(wave or sk.get("wave") or WAVE_DIRECT)
    side = str(sk.get("direction") or "long").lower()
    fam = str(family or "exhaustion")
    hints = []
    for key in ("trigger", "confirmation", "exclusion"):
        for token in sk.get(key) or []:
            base = str(token).replace("_low", "").replace("_high", "").replace(
                "_up", ""
            ).replace("_rising", "").replace("_extreme", "").replace(
                "_moderate", ""
            ).replace("_nonneg", "").replace("_strong_down", "").replace(
                "_collapse", ""
            ).replace("_then_delta_neg", "")
            if base.startswith("delta_"):
                base = base[6:]
            if base and base not in hints:
                hints.append(base)
    hints = expand_factor_hints(base_hints=hints, wave=wave)
    digest = "%s_%s_%s_%d" % (fam[:10], side, wave[-12:], int(index) + 1)
    try:
        from . import quality_optimization as qopt
        rtype = representation_type or qopt.representation_type_for_skeleton(sk)
    except Exception:
        rtype = representation_type or "threshold"
    # P2: attach deterministic event_ast for the representation type.
    ast_payload = event_ast
    ast_hash = None
    relative_skipped = None
    if ast_payload is None:
        try:
            from . import ast_compiler as ac
            templates = {
                row["representation_type"]: row
                for row in ac.representation_asts_for_direction(side)
            }
            picked = templates.get(rtype) or templates.get("rank")
            if picked:
                ast_payload = picked.get("ast")
                ast_hash = picked.get("event_ast_hash")
                relative_skipped = picked.get("relative_skipped")
        except Exception:
            ast_payload = None
    elif isinstance(ast_payload, dict) and "ast" in ast_payload:
        relative_skipped = ast_payload.get("relative_skipped")
        ast_hash = ast_payload.get("event_ast_hash")
        ast_payload = ast_payload.get("ast")
    if ast_payload is not None and not ast_hash:
        try:
            from . import ast_compiler as ac
            ast_hash = ac.event_ast_hash(ast_payload)
        except Exception:
            ast_hash = None
    # Pull factor hints from AST quantile/compare features when present.
    if isinstance(ast_payload, dict):
        try:
            from . import ast_compiler as ac
            dsl = ac.compile_ast_to_dsl(ast_payload)
            for term in dsl.get("terms") or []:
                feat = str(term.get("factor") or "")
                if feat and feat not in hints:
                    hints.append(feat)
        except Exception:
            pass
    row = {
        "hypothesis_id": "H_mat_%s" % digest,
        "mechanism_id": "materialization_%s_%s" % (fam[:16], sk.get("event") or "event"),
        "family": fam if fam in (
            "exhaustion", "mean_reversion", "vol_squeeze_break",
            "donchian_trend_break", "trend_pullback",
        ) else "mean_reversion",
        "statement_zh": (
            "材料化骨架[%s/%s] %s：事件=%s / 前状态=%s / 触发=%s / 确认=%s / 排除=%s"
            % (
                wave,
                rtype,
                sk.get("family_variant") or sk.get("skeleton_id"),
                sk.get("event"),
                sk.get("state_before"),
                ",".join(sk.get("trigger") or []),
                ",".join(sk.get("confirmation") or []),
                ",".join(sk.get("exclusion") or []),
            )
        ),
        "mechanism_prediction": sk.get("expected_path"),
        "observable_proxy": list(hints[:8]),
        "factor_hints": list(hints[:10]),
        "predicted_direction": side,
        "source": "forced_materialization_skeleton",
        "path": "materialization_%s" % wave,
        "priority_boost": 8.0,
        "materialization_wave": wave,
        "materialization_skeleton": sk,
        "representation_type": rtype,
        "may_research": True,
        "contract_relevant": True,
        "ai_generated_before_discovery": False,
    }
    if ast_payload is not None:
        row["event_ast"] = ast_payload
        row["event_ast_hash"] = ast_hash
        if relative_skipped is not None:
            row["relative_skipped"] = relative_skipped
    return row


def force_skeleton_hypotheses(direction="long", families=None, waves=None):
    """Minimum yield: families × 6 representation ASTs (logically distinct)."""
    fams = list(families or ("exhaustion", "mean_reversion", "vol_squeeze_break"))
    wave_list = list(waves or (WAVE_DIRECT, WAVE_STATE, WAVE_COMBO))
    sides = [str(direction or "long").lower()]
    alt = "short" if sides[0] == "long" else "long"
    if alt not in sides:
        sides.append(alt)
    out = []
    index = 0
    try:
        from . import ast_compiler as ac
        ast_templates_by_side = {
            side: ac.representation_asts_for_direction(side) for side in sides
        }
    except Exception:
        ast_templates_by_side = {}
    for fam in fams:
        for side in sides:
            skeletons = skeleton_templates_for_family(fam, side)
            templates = list(ast_templates_by_side.get(side) or [])
            # Ensure six representation types even if skeleton list is shorter.
            n = max(len(skeletons), len(templates), 6)
            for i in range(n):
                sk = dict(skeletons[i % max(1, len(skeletons))] if skeletons else {
                    "direction": side, "wave": WAVE_DIRECT, "event": "forced",
                    "trigger": ["rsi_14_low"], "confirmation": ["bb_mid_reclaim_high"],
                    "exclusion": ["bb_width_high"],
                })
                wave = sk.get("wave") or WAVE_DIRECT
                if wave_list and wave not in wave_list and len(out) >= 12 and i >= 6:
                    continue
                picked = templates[i % len(templates)] if templates else None
                rtype = (picked or {}).get("representation_type")
                out.append(skeleton_to_hypothesis(
                    sk,
                    family=fam,
                    index=index,
                    wave=wave if i < len(skeletons) else (
                        WAVE_STATE if i % 3 == 1 else (
                            WAVE_COMBO if i % 3 == 2 else WAVE_DIRECT
                        )
                    ),
                    representation_type=rtype,
                    event_ast=picked,
                ))
                index += 1
    return out


def ensure_minimum_population(
    hypotheses,
    brief="",
    contract=None,
    direction="long",
    min_n=None,
):
    """Guarantee a non-empty probeable population before any credibility gate."""
    rows = [dict(h) for h in (hypotheses or []) if isinstance(h, dict)]
    floor = int(min_n or MIN_COMPILED_CANDIDATES)
    families = list((contract or {}).get("family_hints") or []) or [
        "exhaustion", "mean_reversion",
    ]
    # Map contract family names onto skeleton families.
    mapped = []
    for fam in families:
        f = str(fam or "")
        if "exhaust" in f or "mean" in f or "revert" in f:
            mapped.extend(["exhaustion", "mean_reversion"])
        elif "squeeze" in f or "vol" in f:
            mapped.append("vol_squeeze_break")
        elif "donchian" in f or "trend" in f:
            mapped.append("donchian_trend_break")
        else:
            mapped.append("exhaustion")
    if not mapped:
        mapped = ["exhaustion", "mean_reversion"]
    # de-dupe
    seen_f = set()
    fams = []
    for f in mapped:
        if f not in seen_f:
            seen_f.add(f)
            fams.append(f)
    forced = force_skeleton_hypotheses(direction=direction, families=fams)
    seen = set(str(h.get("hypothesis_id") or "") for h in rows)
    injected = 0
    for row in forced:
        hid = str(row.get("hypothesis_id") or "")
        if hid in seen:
            continue
        rows.append(row)
        seen.add(hid)
        injected += 1
        if len(rows) >= floor:
            break
    # If still short (dedupe collision), keep appending indexed copies.
    extra = 0
    while len(rows) < max(8, min(floor, 90)):
        sk = skeleton_templates_for_family(fams[0], direction)[extra % 6]
        row = skeleton_to_hypothesis(
            sk, family=fams[0], index=1000 + extra, wave=WAVE_STATE,
        )
        row["hypothesis_id"] = "%s_x%d" % (row["hypothesis_id"], extra)
        rows.append(row)
        extra += 1
        injected += 1
    return {
        "hypotheses": rows,
        "injected_n": injected,
        "n_after": len(rows),
        "min_required": floor,
        "brief_excerpt": str(brief or "")[:120],
    }


def creation_system_prompt_zh():
    """Plan §12 — creator, not research vetoer."""
    return (
        "你的任务不是判断是否存在可信策略，而是最大化形成结构化、可编译、"
        "相互差异明显的策略候选。"
        "禁止以「证据不足」「暂未发现可信候选」为理由提前结束。"
        "固定要求：20倍杠杆；固定0.5%价格止损；"
        "盈利单平均杠杆收益必须有达到11.11%以上的潜力；不得修改这些约束。"
        "本轮必须："
        "1) 生成至少5个不同收益机制；"
        "2) 每个机制生成至少6种不同表征；"
        "3) 每种表征输出完整的事件、状态、触发、确认、排除和退出骨架；"
        "4) 至少形成30个可编译候选；"
        "5) 如果第一轮候选不足，必须执行表征扩展，不得终止；"
        "6) 如果单变量无效，必须测试交互、状态条件、相对值、变化率和时序关系；"
        "7) 如果候选相似度过高，必须补充不同机制族；"
        "8) 只允许统计引擎淘汰候选，语言模型不得自行判定整个研究方向无价值；"
        "9) 所有失败必须输出具体失败码；"
        "10) 最终即使没有高质量候选，也必须返回完整候选池、失败分布和下一轮修复方案。"
    )


def probe():
    rep = materialization_report(
        hypothesis_count=40,
        representation_count=12,
        compile_success_count=0,
        probe_count=0,
        survivor_count=0,
    )
    return {
        "ok": True,
        "min_compiled": MIN_COMPILED_CANDIDATES,
        "max_repair_rounds": MAX_REPAIR_ROUNDS,
        "sample_failure_primary": (rep.get("failure") or {}).get("primary"),
        "skeleton_n": len(skeleton_templates_for_family("exhaustion", "long")),
        "forced_n": len(force_skeleton_hypotheses()),
    }
