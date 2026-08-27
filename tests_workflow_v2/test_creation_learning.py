# -*- coding: utf-8 -*-
from __future__ import print_function

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import parallel_creation as pc
from dual_engine_workflow_v2.creation_attribution import (
    MIN_JOBS,
    MIN_SYMBOLS,
    build_clusters,
    cluster_id_for,
)
from dual_engine_workflow_v2.creation_case_store import (
    extract_case,
    kimi_receipt_evidence,
    record_creation_outcome,
    save_case,
)
from dual_engine_workflow_v2.creation_learning_pack import (
    public_creator_library,
    refresh_pack,
    set_human_veto,
)
from dual_engine_workflow_v2.creation_live_lessons import (
    attribute_entry_situations,
    summarize_live_family_paths,
)
from dual_engine_workflow_v2.entry_structure_literacy import PRODUCTION_ERROR_CASES
from dual_engine_workflow_v2.quality_gate import (
    SINGLE_STRATEGY_GATE_RULES,
    STRUCTURE_CHECKS,
)


FROZEN_GATE_RULES = {
    "min_raw_expectancy": 0.003,
    "min_weekly_opens": 0.50,
    "min_total_trades": 30,
    "min_payoff_ratio": 1.15,
    "min_win_rate": 0.38,
    "min_holding_bars": 4,
    "min_tp_trigger_rate": 0.25,
    "max_unleveraged_drawdown": 0.15,
    "min_t_statistic": 1.35,
    "min_in_sample_sharpe": 0.90,
    "min_out_of_sample_sharpe": 0.50,
    "min_sharpe_retention_ratio": 0.30,
    "max_sharpe_decay_rate": 0.70,
    "stress_friction_rate": 0.0016,
    "is_stressed_expectancy_diagnostic_only": True,
    "oos_sharpe_retention_diagnostic_only": True,
    "oos_stressed_expectancy_diagnostic_only": True,
    "min_oos_stressed_expectancy": 0.0,
}

FROZEN_STRUCTURE_CODES = (
    "win_rate_payoff_combo_failed",
    "structure_cannot_read_trend",
    "no_trend_location_skill",
    "oos_zero_trades",
    "oos_backtest_missing",
)

FROZEN_ERROR_CASE_TITLES = (
    "CL 超卖反转（优化版V2）",
    "天然气冲高衰竭与动量衰减做空",
    "弱势反弹失败二次探底做空",
    "流动性扫荡后 reclaim 反转",
    "高量冲高承接不足回落做空",
)


def _osc_ast():
    return {
        "type": "all",
        "children": [
            {"type": "compare", "feature": "rsi_14", "op": "lt", "value": 30.0},
            {"type": "compare", "feature": "close_z_20", "op": "lt", "value": -1.0},
        ],
    }


def _kimi_fail_result(symbol="PLTR-USDT-SWAP", direction="short", title="回踩后做空"):
    return {
        "ok": False,
        "technical_completed": True,
        "outcome": "candidate_quality_failure",
        "symbol": symbol,
        "direction": direction,
        "brief": "方向型：1h空头结构破位确认后顺势做空-%s独立创造（不要赔率型）" % symbol,
        "attempts": [{
            "candidate": {
                "symbol": symbol,
                "timeframe": "1h",
                "direction": direction,
                "title_zh": title,
                "entry_ast": _osc_ast(),
                "protective_stop_pct": 0.012,
            },
            "evaluation": {
                "quality_gate": {
                    "ok": False,
                    "failed_rules": [
                        "holding_bars_below_threshold",
                        "tp_trigger_rate_below_threshold",
                    ],
                    "reasons": [
                        "holding_bars_below_threshold",
                        "tp_trigger_rate_below_threshold",
                    ],
                    "metrics": {
                        "E_raw": 0.01,
                        "weekly_opens": 0.8,
                        "n": 40,
                        "R": 1.4,
                        "win_rate": 0.45,
                        "avg_holding_bars": 2.0,
                        "tp_trigger_rate": 0.10,
                        "max_drawdown": 0.08,
                        "t_statistic": 1.8,
                        "annualized_sharpe": 1.1,
                    },
                    "structure_literacy": {
                        "ok": False,
                        "family": "no_trend_mr",
                        "features": ["rsi_14", "close_z_20"],
                    },
                },
            },
        }],
    }


class TempRootCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qiyu_learn_")
        self.env = mock.patch.dict(os.environ, {"VECTOR_ROOT": self.temp.name}, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()


class TestIngestShapedKimiResult(TempRootCase):
    def test_extract_from_top_level_attempts_like_kimi_runtime(self):
        """KimiCreatorRuntime puts attempts on the result root (and cognitive)."""
        result = _kimi_fail_result("ETH-USDT-SWAP")
        job = {
            "job_id": "creation_smoke_kimi_shape",
            "symbol": "ETH-USDT-SWAP",
            "timeframe": "1h",
            "trade_direction": "short",
            "research_direction": "方向型：1h趋势破位顺势做空",
        }
        case = extract_case(job, result)
        self.assertIn("holding_bars_below_threshold", case.get("failed_rules") or [])
        self.assertEqual(case.get("structure_family"), "no_trend_mr")
        got = record_creation_outcome(job, result)
        self.assertTrue(got.get("ok"))
        self.assertEqual(got.get("job_id"), "creation_smoke_kimi_shape")
        lib = public_creator_library()
        self.assertTrue(lib.get("not_a_gate"))
        self.assertEqual(lib.get("schema"), "qiyu_creation_learning_pack_v1")

    def test_learning_disabled_skips_write(self):
        with mock.patch.dict(os.environ, {"QIYU_CREATION_LEARNING": "0"}):
            got = record_creation_outcome(
                {"job_id": "creation_disabled", "symbol": "ETH-USDT-SWAP"},
                _kimi_fail_result(),
            )
            self.assertEqual(got.get("skipped"), "QIYU_CREATION_LEARNING=0")
            lib = public_creator_library()
            self.assertTrue(lib.get("disabled"))
            self.assertEqual((lib.get("case_library") or {}).get("clusters") or [], [])


class TestCompactKimiFailureEvidence(unittest.TestCase):
    def test_kimi_quality_failure_receipt_keeps_failed_rules(self):
        compact = pc._compact_result(_kimi_fail_result())
        evidence = compact["failure_evidence"]
        self.assertTrue(evidence)
        self.assertIn("holding_bars_below_threshold", evidence.get("failed_rules") or [])
        self.assertEqual(evidence.get("structure_family"), "no_trend_mr")
        self.assertTrue((evidence.get("near_miss") or {}).get("ok"))
        self.assertTrue(evidence.get("not_a_gate"))

    def test_kimi_receipt_helper_omits_oos_numbers(self):
        blob = json.dumps(kimi_receipt_evidence(_kimi_fail_result()), ensure_ascii=False)
        self.assertNotIn("oos_sharpe", blob)


class TestCaseStoreAndAttribution(TempRootCase):
    def test_extract_archives_direction_but_cluster_ignores_it(self):
        job = {
            "job_id": "creation_test_a",
            "symbol": "A-USDT-SWAP",
            "timeframe": "1h",
            "trade_direction": "short",
            "research_direction": "方向型：1h回踩后做空-A独立创造（不要赔率型）",
        }
        case = extract_case(job, _kimi_fail_result("A-USDT-SWAP"))
        self.assertEqual(case["schema"], "qiyu_creation_case_v1")
        self.assertIn("回踩后做空", case.get("research_direction") or "")
        self.assertTrue(case.get("research_direction_not_a_cluster_key"))
        cid = cluster_id_for(case)
        self.assertNotIn("回踩", cid)
        self.assertNotIn("research_direction", cid)
        self.assertIn("no_trend_mr", cid)

    def test_single_symbol_ten_fails_not_promoted(self):
        cases = []
        for i in range(10):
            cases.append({
                "job_id": "job_same_%s" % i,
                "symbol": "AAA-USDT-SWAP",
                "outcome": "candidate_quality_failure",
                "structure_family": "no_trend_mr",
                "feature_fingerprint": "osc",
                "failed_rules": ["holding_bars_below_threshold"],
                "research_direction": "方向型：1h回踩后做空",
            })
        clusters = build_clusters(cases)
        self.assertTrue(clusters)
        self.assertFalse(clusters[0]["promoted"])
        self.assertEqual(clusters[0]["n_symbols"], 1)

    def test_three_symbols_five_jobs_promoted_without_direction_ban(self):
        symbols = ["AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP"]
        cases = []
        for i in range(5):
            cases.append({
                "job_id": "job_cross_%s" % i,
                "symbol": symbols[i % 3],
                "outcome": "candidate_quality_failure",
                "structure_family": "no_trend_mr",
                "feature_fingerprint": "osc",
                "failed_rules": [
                    "holding_bars_below_threshold",
                    "tp_trigger_rate_below_threshold",
                ],
                "research_direction": "方向型：1h回踩后做空-独立创造",
            })
        clusters = build_clusters(cases)
        promoted = [row for row in clusters if row.get("promoted")]
        self.assertEqual(len(promoted), 1)
        lesson = promoted[0]["lesson_zh"]
        self.assertIn("共同因子", lesson)
        self.assertIn("不是研究方向禁令", lesson)
        self.assertNotIn("回踩后做空以后不要再做", lesson)
        self.assertTrue(promoted[0]["not_a_research_direction_ban"])
        self.assertGreaterEqual(promoted[0]["n_symbols"], MIN_SYMBOLS)
        self.assertGreaterEqual(promoted[0]["n_jobs"], MIN_JOBS)

    def test_record_and_public_library(self):
        symbols = ["AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP"]
        for i in range(5):
            job = {
                "job_id": "creation_lib_%s" % i,
                "symbol": symbols[i % 3],
                "timeframe": "1h",
                "trade_direction": "short",
                "research_direction": "方向型：1h回踩后做空",
            }
            record_creation_outcome(job, _kimi_fail_result(symbols[i % 3]))
        lib = public_creator_library()
        self.assertTrue(lib["not_a_gate"])
        self.assertTrue(lib["quality_gate_unchanged"])
        clusters = (lib.get("case_library") or {}).get("clusters") or []
        self.assertTrue(clusters)
        self.assertTrue(clusters[0]["not_a_gate"])
        self.assertIn("共同因子", clusters[0]["lesson_zh"])
        order = lib.get("advisory_check_order") or {}
        self.assertTrue(order.get("not_a_gate"))
        self.assertTrue(order.get("quality_gate_unchanged"))
        blob = json.dumps(lib, ensure_ascii=False)
        self.assertNotIn("weekly_geometric_growth", blob)
        self.assertIn("occupancy_geo_excluded", blob)

    def test_human_veto_hides_cluster(self):
        symbols = ["AAA-USDT-SWAP", "BBB-USDT-SWAP", "CCC-USDT-SWAP"]
        cases = []
        for i in range(5):
            case = {
                "job_id": "veto_%s" % i,
                "symbol": symbols[i % 3],
                "outcome": "candidate_quality_failure",
                "structure_family": "no_trend_mr",
                "feature_fingerprint": "osc",
                "failed_rules": ["no_trend_location_skill"],
            }
            save_case(case)
            cases.append(case)
        pack = refresh_pack(cases=cases, live_paths={
            "occupancy_geo_excluded": True, "by_family": {},
        }, apply_beliefs=False)
        cid = pack["clusters"][0]["cluster_id"]
        self.assertTrue(pack["clusters"][0]["promoted"])
        set_human_veto(cid, True)
        pack2 = refresh_pack(cases=cases, live_paths={
            "occupancy_geo_excluded": True, "by_family": {},
        }, apply_beliefs=False)
        self.assertIn(cid, pack2.get("human_vetoes") or [])
        lib = public_creator_library()
        ids = [
            row.get("cluster_id")
            for row in ((lib.get("case_library") or {}).get("clusters") or [])
        ]
        self.assertNotIn(cid, ids)
        report = Path(self.temp.name) / "auto_trade" / "dual_engine" / "creation_learning" / "cluster_report.md"
        self.assertTrue(report.is_file())
        text = report.read_text(encoding="utf-8")
        self.assertIn("人否决", text)
        self.assertIn("不能放行", text)

    def test_success_few_shot(self):
        for i in range(2):
            save_case({
                "job_id": "ready_%s" % i,
                "symbol": "ETH-USDT-SWAP" if i == 0 else "BTC-USDT-SWAP",
                "outcome": "candidate_ready",
                "structure_family": "trend_break",
                "feature_fingerprint": "trend+break",
                "features": ["donchian20_long_break", "trend_bias_50_200"],
            })
        pack = refresh_pack(live_paths={
            "occupancy_geo_excluded": True, "by_family": {},
        }, apply_beliefs=False)
        shots = pack.get("success_few_shot") or []
        self.assertTrue(shots)
        self.assertIn("trend_break", shots[0]["structure_family"])
        self.assertNotIn("protective_stop_pct", json.dumps(shots, ensure_ascii=False))


class TestLiveFamilyPaths(TempRootCase):
    def test_path_rates_exclude_occupancy(self):
        auto = Path(self.temp.name) / "auto_trade"
        auto.mkdir(parents=True)
        (auto / "live_structure_classification.json").write_text(json.dumps({
            "rows": [
                {"strategy_key": "kimi_aaa", "family": "no_trend_mr"},
                {"strategy_key": "kimi_bbb", "family": "trend_break"},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        (auto / "formal_v6_state.json").write_text(json.dumps({
            "history": [
                {"strategy_key": "kimi_aaa", "first_touch": "stop", "close_reason": "protective"},
                {"strategy_key": "kimi_aaa", "first_touch": "stop", "close_reason": "protective"},
                {"strategy_key": "kimi_aaa", "first_touch": "stop", "close_reason": "protective"},
                {"strategy_key": "kimi_aaa", "first_touch": "stop", "close_reason": "protective"},
                {"strategy_key": "kimi_aaa", "first_touch": "stop", "close_reason": "protective"},
                {"strategy_key": "kimi_bbb", "first_touch": "target", "close_reason": "take_profit"},
                {"strategy_key": "kimi_bbb", "first_touch": "target", "close_reason": "take_profit"},
                {"strategy_key": "kimi_bbb", "first_touch": "target", "close_reason": "take_profit"},
                {"strategy_key": "kimi_bbb", "first_touch": "horizon", "close_reason": "timed"},
                {"strategy_key": "kimi_bbb", "first_touch": "target", "close_reason": "take_profit"},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        paths = summarize_live_family_paths()
        self.assertTrue(paths["occupancy_geo_excluded"])
        self.assertIn("no_trend_mr", paths["by_family"])
        self.assertEqual(paths["by_family"]["no_trend_mr"]["止损"], 5)
        self.assertEqual(paths["by_family"]["trend_break"]["止盈"], 4)
        for key in (
            "weekly_geometric_growth", "weekly_geometric_growth_oos", "occupancy_replay",
        ):
            self.assertNotIn(key, paths.get("by_family") or {})
            self.assertIsNone(paths.get(key))
        pack = refresh_pack(cases=[], live_paths=paths, apply_beliefs=True)
        self.assertTrue(pack["occupancy_geo_excluded"])
        self.assertEqual(pack["live_family_paths"]["by_family"]["no_trend_mr"]["止损"], 5)


class TestLiveEntrySituations(TempRootCase):
    def _close(self, key, path, atr, opened_at, trend, side="long", adx=40.0, i=0):
        first = {"止损": "stop", "止盈": "target", "定时": "horizon"}[path]
        return {
            "strategy_key": key,
            "side": side,
            "first_touch": first,
            "opened_at": opened_at,
            "opened_at_ts": 1000.0 + i,
            "closed_at": opened_at,
            "closed_at_ts": 1000.0 + i + 3600,
            "entry_data": {
                "entry_atr_pct": atr,
                "trend_bias_50_200": trend,
                "adx_14": adx,
            },
        }

    def test_same_strategy_contrasts_stop_vs_take_profit_after_three_repeats(self):
        closes = []
        for i in range(3):
            closes.append(self._close(
                "kimi_aaa", "止损", 0.03, "2026-08-26 22:10:00", -0.02, i=i,
            ))
        for i in range(3):
            closes.append(self._close(
                "kimi_aaa", "止盈", 0.01, "2026-08-26 10:10:00", 0.02, i=10 + i,
            ))
        got = attribute_entry_situations(closes, family_by_key={"kimi_aaa": "trend_aligned"})
        sits = got.get("situations") or []
        self.assertGreaterEqual(len(sits), 2)
        paths = sorted(row["dominant_path"] for row in sits)
        self.assertEqual(paths, ["止损", "止盈"])
        attrs = got.get("attributions") or []
        self.assertTrue(attrs)
        lesson = attrs[0]["lesson_zh"]
        self.assertIn("同一策略", lesson)
        self.assertIn("对照", lesson)
        self.assertIn("归因", lesson)
        self.assertIn("高波动", lesson)
        self.assertIn("中波动", lesson)
        self.assertIn("不是该方向本身不能做", attrs[0]["reason_zh"] + lesson)
        self.assertTrue(attrs[0]["not_a_research_direction_ban"])
        pack = refresh_pack(cases=[], live_paths=summarize_live_family_paths(
            closes=closes, family_by_key={"kimi_aaa": "trend_aligned"},
        ), apply_beliefs=False)
        pub = (pack.get("live_family_paths") or {}).get("entry_situations") or {}
        self.assertTrue(pub.get("attributions"))
        lib = public_creator_library()
        live = (lib.get("live_family_paths") or {}).get("entry_situations") or {}
        self.assertTrue(live.get("attributions"))
        self.assertTrue(live.get("not_a_gate"))
        report = Path(self.temp.name) / "auto_trade" / "dual_engine" / "creation_learning" / "cluster_report.md"
        self.assertIn("入场点", report.read_text(encoding="utf-8"))

    def test_two_repeats_do_not_promote(self):
        closes = [
            self._close("kimi_aaa", "止损", 0.03, "2026-08-26 22:10:00", -0.02, i=0),
            self._close("kimi_aaa", "止损", 0.03, "2026-08-26 22:10:00", -0.02, i=1),
        ]
        got = attribute_entry_situations(closes)
        self.assertEqual(got.get("situations") or [], [])
        self.assertEqual(got.get("attributions") or [], [])

    def test_mixed_paths_below_concentration_do_not_promote(self):
        closes = []
        for i in range(2):
            closes.append(self._close(
                "kimi_aaa", "止损", 0.03, "2026-08-26 22:10:00", -0.02, i=i,
            ))
        for i in range(2):
            closes.append(self._close(
                "kimi_aaa", "止盈", 0.03, "2026-08-26 22:10:00", -0.02, i=10 + i,
            ))
        got = attribute_entry_situations(closes)
        self.assertEqual(got.get("situations") or [], [])

    def test_common_factor_across_two_strategies(self):
        closes = []
        for key in ("kimi_aaa", "kimi_bbb"):
            for i in range(3):
                closes.append(self._close(
                    key, "止损", 0.03, "2026-08-26 22:10:00", -0.02, i=i,
                ))
        got = attribute_entry_situations(closes)
        common = got.get("common_factors") or []
        self.assertTrue(common)
        self.assertGreaterEqual(common[0]["n_strategies"], 2)
        self.assertIn("共同因子", common[0]["lesson_zh"])
        self.assertNotIn("研究方向禁令不要", common[0]["lesson_zh"])


class TestGateAndStaticDoctrineLocked(unittest.TestCase):
    def test_quality_gate_thresholds_unchanged(self):
        self.assertEqual(SINGLE_STRATEGY_GATE_RULES, FROZEN_GATE_RULES)
        self.assertEqual(
            tuple(item[0] for item in STRUCTURE_CHECKS),
            FROZEN_STRUCTURE_CODES,
        )

    def test_production_error_cases_not_rewritten(self):
        titles = tuple(row["title_zh"] for row in PRODUCTION_ERROR_CASES)
        self.assertEqual(titles, FROZEN_ERROR_CASE_TITLES)

    def test_creator_prompt_forbids_direction_ban(self):
        text = Path(ROOT, "dual_engine_workflow_v2", "kimi_creator_runtime.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("不是研究方向禁令", text)
        self.assertIn("不等于以后不能做空", text)
        self.assertIn("不能放行", text)
        self.assertIn("入场点课", text)
        self.assertIn("反复≥3次才采信", text)
        self.assertIn("二次回炉", text)
        self.assertIn("live_parent", text)
        self.assertIn("禁止抄止损/止盈精确小数", text)


if __name__ == "__main__":
    unittest.main()
