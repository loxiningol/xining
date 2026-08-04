# -*- coding: utf-8 -*-
"""质量教义：胜率硬底 + 小样本前向/门槛2/蒙特卡洛分层。"""
from __future__ import print_function

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import creation_blueprint as blueprint
from dual_engine_workflow_v2 import creation_prelim_eval as prelim
from dual_engine_workflow_v2 import creation_quality_doctrine as doctrine
from dual_engine_workflow_v2 import fitness_engine as fitness
from dual_engine_workflow_v2 import funnel_l3_null_hypothesis as l3
from dual_engine_workflow_v2 import gates


class TestWinRateHardFloor(unittest.TestCase):
    """垃圾书：胜率 28% 即使平均净收益好看也必须拒。"""

    def test_lottery_book_rejected_by_admission(self):
        # 胜率 28%：7 亏 3 盈，肥尾单笔撑起均值
        rets = [-0.02] * 7 + [0.20, 0.15, 0.12]
        q = blueprint._admission_trade_quality(rets)
        self.assertFalse(q["ok"])
        self.assertTrue(any("胜率" in r for r in q["reasons"]))

    def test_remove_max_win_collapse_rejected(self):
        # 胜率高但去掉最大盈利后崩溃
        rets = [0.50, -0.01, -0.01, -0.01, -0.01, -0.01, -0.01, 0.01]
        q = blueprint._admission_trade_quality(rets)
        self.assertFalse(q["ok"])
        self.assertTrue(any("去最大盈利" in r for r in q["reasons"]))

    def test_prelim_requires_win_rate_strictly_above_50(self):
        v = prelim.prelim_eval({"win_rate": 0.50, "n_trades": 20})
        self.assertFalse(v["present_to_human"])
        self.assertIn("win_rate_not_above_50pct", v["reject_reasons"])
        self.assertIn("胜率", v["min_win_rate_rule_zh"])

    def test_prelim_high_win_rate_presentable(self):
        v = prelim.prelim_eval({"win_rate": 0.75, "n_trades": 8})
        self.assertTrue(v["present_to_human"])


class TestSmallNForward(unittest.TestCase):
    """crec 类：8 笔、8 折中 6 折为正 → 前向应过。"""

    def test_doctrine_ratio_pass_6_of_8(self):
        ok, detail = doctrine.wf_ok_small_n(6, 8, 8)
        self.assertTrue(ok)
        self.assertIn("前向稳健", detail.get("label_zh") or "")

    def test_gate3_small_n_not_killed_by_empty_windows(self):
        # 8 笔：6 盈 2 亏，时间序上多数窗期望为正
        trades = [{"pnl_ratio": x} for x in (
            0.03, 0.02, -0.01, 0.04, 0.02, 0.01, -0.01, 0.03,
        )]
        wf = l3.walk_forward_windows(trades)
        self.assertTrue(wf.get("small_sample"))
        self.assertEqual(wf.get("门槛名"), "门槛3")
        self.assertTrue(wf.get("pass"), msg=wf.get("label_zh") or wf)
        g3 = gates.evaluate_gate3(wf, trades=None, null_hypothesis={"pass": True})
        self.assertTrue(g3["pass"], msg=g3.get("evidence"))
        self.assertEqual(g3["evidence"].get("门槛名"), "门槛3")

    def test_large_n_still_needs_7_of_10(self):
        # 20 笔但多数窗期望为负 → 应不过绝对门
        trades = [{"pnl_ratio": -0.01} for _ in range(20)]
        wf = l3.walk_forward_windows(trades)
        self.assertFalse(wf.get("small_sample"))
        self.assertFalse(wf.get("pass"))


class TestGate2SampleAware(unittest.TestCase):
    def test_small_floors(self):
        f = doctrine.gate2_floors(8)
        self.assertTrue(f["small_sample"])
        self.assertAlmostEqual(f["calmar_min"], 1.0)
        self.assertAlmostEqual(f["payoff_min"], 1.8)
        self.assertIn("第二步", f["label_zh"])

    def test_large_floors(self):
        f = doctrine.gate2_floors(20)
        self.assertFalse(f["small_sample"])
        self.assertAlmostEqual(f["calmar_min"], 1.5)
        self.assertAlmostEqual(f["payoff_min"], 2.5)

    def test_fitness_labels_zh(self):
        trades = [{"pnl_ratio": 0.05} for _ in range(10)]
        out = fitness.evaluate_multi_objective(trades, enforce=True)
        self.assertEqual(out.get("门槛名"), "门槛2")
        self.assertIn("胜率", out["metrics"])
        self.assertIn("门槛2", out["metrics"])


class TestMonteCarloTier(unittest.TestCase):
    def test_small_n_beat_70(self):
        self.assertAlmostEqual(doctrine.mc_beat_threshold(8), 0.70)

    def test_large_n_beat_90(self):
        self.assertAlmostEqual(doctrine.mc_beat_threshold(25), 0.90)

    def test_gate5_uses_tier(self):
        g = gates.evaluate_gate5(
            {"accept_pct_observed": 0.75, "n": 8},
            {"sharpe": 0.1, "mean_net": 0.01},
        )
        self.assertTrue(g["pass"])
        self.assertAlmostEqual(g["evidence"]["蒙特卡洛击败率要求"], 0.70)


class TestFrost2QuickDoctrine(unittest.TestCase):
    def test_frost2_wf_helper_accepts_crec_shape(self):
        import frost2_action_run as f2
        ok, detail = f2._wf_ok_small_n({
            "trades": 8, "folds": 8, "fold_positive": 6,
        })
        self.assertTrue(ok)
        self.assertTrue(detail.get("pass"))


class TestPipelineOrderZh(unittest.TestCase):
    """叙述顺序必须是：指令 → 研究发现 → 统一门槛 → 四阶段复核。"""

    def test_three_steps_in_order(self):
        order = doctrine.pipeline_order_zh()
        self.assertEqual(order[0], "创造策略指令")
        self.assertIn("第一步", order[1])
        self.assertIn("研究发现", order[1])
        self.assertIn("第二步", order[2])
        self.assertIn("门槛", order[2])
        self.assertIn("寒霜贰", order[2])
        self.assertIn("第三步", order[3])
        self.assertIn("复核", order[3])

    def test_summary_leads_with_order(self):
        s = doctrine.doctrine_summary_zh()
        self.assertIn("创造管道顺序", s)
        self.assertEqual(s["第一步"], "研究发现：委员会 + 探针 + 多重检验")
        self.assertIn("门槛统一", s["第二步"])
        self.assertIn("四阶段复核", s["第三步"])


if __name__ == "__main__":
    unittest.main()
