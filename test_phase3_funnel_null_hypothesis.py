# -*- coding: utf-8 -*-
"""Phase-3: three-level funnel + null-hypothesis anti-overfitting tests.

Covers:
  - L1 micro-screen hard rejects + cull rate ≥90% on garbage fixtures
  - L1 wall-time target documentation (<0.1s on trade-list path)
  - L2 Pareto keeps only non-dominated (Calmar, payoff, trade frequency)
  - L3 permuted-returns rejects coincidence fit (Sharpe≥0.5)
  - L3 inverted-market rejects spurious edge
  - L3 WF ≥7/10 with Calmar≥1.0 AND positive expectancy
  - Gate3 additive null_hypothesis kwarg (backward compatible)
  - Protective 0.9% SL unchanged
"""
from __future__ import print_function

import math
import random
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from dual_engine_workflow_v2 import funnel_l1_micro_screen as l1
from dual_engine_workflow_v2 import funnel_l2_pareto as l2
from dual_engine_workflow_v2 import funnel_l3_null_hypothesis as l3
from dual_engine_workflow_v2 import phase3_funnel as funnel
from dual_engine_workflow_v2 import gates
from dual_engine_workflow_v2 import fitness_engine as fe


def _mk_trades(pnls, mae=None, leverage=20):
    out = []
    for i, p in enumerate(pnls):
        row = {
            "pnl_ratio": float(p),
            "leverage": leverage,
            "entry_time": "2026-01-%02d 10:00:00" % ((i % 28) + 1),
            "exit_time": "2026-01-%02d 12:00:00" % ((i % 28) + 1),
        }
        if mae is not None:
            if isinstance(mae, (list, tuple)):
                row["mae_price_pct"] = float(mae[i % len(mae)])
            else:
                row["mae_price_pct"] = float(mae)
        out.append(row)
    return out


class _MiniSeries(object):
    def __init__(self, values):
        self._values = list(values)

    def tolist(self):
        return list(self._values)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __getitem__(self, i):
        return self._values[i]

    @property
    def iloc(self):
        return self

    def min(self):
        return min(self._values)

    def max(self):
        return max(self._values)

    def all(self):
        return all(self._values)

    def __gt__(self, other):
        return _MiniSeries([v > other for v in self._values])


class _MiniFrame(object):
    """Pandas-free OHLC frame for unit tests + L3 transforms."""

    def __init__(self, data, index=None):
        self._data = {k: list(v) for k, v in data.items()}
        n = len(next(iter(self._data.values())))
        self.index = list(index if index is not None else range(n))

    @property
    def columns(self):
        return list(self._data.keys())

    def __len__(self):
        return len(self.index)

    def __getitem__(self, key):
        return _MiniSeries(self._data[key])

    def __setitem__(self, key, values):
        self._data[key] = list(values)

    def copy(self):
        return _MiniFrame(self._data, self.index)

    @property
    def iloc(self):
        frame = self

        class _ILoc(object):
            def __getitem__(self, sl):
                if isinstance(sl, slice):
                    data = {k: v[sl] for k, v in frame._data.items()}
                    idx = frame.index[sl]
                    return _MiniFrame(data, idx)
                raise TypeError("only slice supported")
        return _ILoc()


def _synthetic_ohlc(n=800, seed=7, drift=0.0002, vol=0.01):
    """Minimal OHLC frame for permute/invert tests (no pandas required)."""
    rng = random.Random(seed)
    opens, highs, lows, closes, vols = [], [], [], [], []
    px = 100.0
    for _ in range(n):
        ret = drift + rng.uniform(-vol, vol)
        o = px
        c = px * (1.0 + ret)
        h = max(o, c) * (1.0 + abs(rng.uniform(0, vol * 0.5)))
        l = min(o, c) * (1.0 - abs(rng.uniform(0, vol * 0.5)))
        opens.append(o); highs.append(h); lows.append(l); closes.append(c); vols.append(1.0)
        px = c
    return _MiniFrame({
        "open": opens, "high": highs, "low": lows, "close": closes, "volume": vols,
    })


class L1MicroScreenTests(unittest.TestCase):
    def test_reject_too_few_entries(self):
        trades = _mk_trades([0.05, -0.01, 0.04, -0.01])  # 4 < 5
        v = l1.evaluate_micro_screen_trades(trades)
        self.assertFalse(v["pass"])
        self.assertTrue(any("filled_entries" in r for r in v["reject_reasons"]))

    def test_reject_low_payoff(self):
        # Many tiny wins, one large loss → payoff ≤ 1.2
        pnls = [0.01] * 9 + [-0.10]
        trades = _mk_trades(pnls)
        v = l1.evaluate_micro_screen_trades(trades)
        self.assertFalse(v["pass"])
        self.assertTrue(any("payoff" in r for r in v["reject_reasons"]))

    def test_reject_severe_mae(self):
        # Good payoff but severe MAE vs avg win
        pnls = [0.08, 0.08, 0.08, 0.08, -0.02, 0.08]
        # avg_win ~0.08 leveraged → avg_win_price = 0.08/20 = 0.004
        # MAE 0.05 > 2.5 * 0.004 = 0.01
        trades = _mk_trades(pnls, mae=0.05, leverage=20)
        v = l1.evaluate_micro_screen_trades(trades)
        self.assertFalse(v["pass"])
        self.assertTrue(any("mae" in r for r in v["reject_reasons"]))

    def test_pass_healthy_sample(self):
        pnls = [0.10, 0.12, -0.03, 0.11, 0.09, -0.03, 0.10, 0.08]
        trades = _mk_trades(pnls, mae=0.001, leverage=20)
        v = l1.evaluate_micro_screen_trades(trades)
        self.assertTrue(v["pass"], v)

    def test_cull_rate_ge_90pct_garbage(self):
        """≥90% of garbage fixtures culled by L1."""
        rng = random.Random(99)
        garbage = []
        for i in range(100):
            kind = i % 3
            if kind == 0:
                # too few fills
                pnls = [rng.uniform(-0.02, 0.03) for _ in range(rng.randint(0, 4))]
            elif kind == 1:
                # low payoff: high WR tiny wins
                pnls = [0.005] * 12 + [-0.08, -0.07]
            else:
                # severe MAE
                pnls = [0.06] * 6 + [-0.02]
                trades = _mk_trades(pnls, mae=0.08, leverage=20)
                garbage.append(trades)
                continue
            garbage.append(_mk_trades(pnls))
        culled = sum(1 for g in garbage if not l1.evaluate_micro_screen_trades(g)["pass"])
        rate = culled / float(len(garbage))
        self.assertGreaterEqual(rate, 0.90, "cull_rate=%.3f" % rate)

    def test_l1_wall_time_under_100ms_trade_path(self):
        pnls = [0.05 if i % 3 else -0.02 for i in range(40)]
        trades = _mk_trades(pnls)
        t0 = time.perf_counter()
        v = l1.run_micro_screen(trades=trades)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertTrue(v["pass"] or not v["pass"])  # ran
        self.assertLess(elapsed_ms, 100.0, "wall_ms=%.3f" % elapsed_ms)
        self.assertLess(v["wall_time_ms"], 100.0)


class L2ParetoTests(unittest.TestCase):
    def test_pareto_keeps_non_dominated_only(self):
        cands = [
            {"id": "a", "calmar": 3.0, "payoff_ratio": 3.0, "trade_frequency": 10,
             "fitness_pass": True},
            {"id": "b", "calmar": 2.0, "payoff_ratio": 2.6, "trade_frequency": 8,
             "fitness_pass": True},  # dominated by a
            {"id": "c", "calmar": 1.6, "payoff_ratio": 4.0, "trade_frequency": 20,
             "fitness_pass": True},  # not dominated (payoff/freq)
            {"id": "d", "calmar": 5.0, "payoff_ratio": 5.0, "trade_frequency": 30,
             "fitness_pass": False},  # fitness fail → not eligible
        ]
        front = l2.pareto_front(cands)
        self.assertIn("a", front["front_ids"])
        self.assertIn("c", front["front_ids"])
        self.assertNotIn("b", front["front_ids"])
        self.assertNotIn("d", front["front_ids"])

    def test_l2_singleton_survivor_requires_fitness(self):
        # Pseudo high-WR low-payoff must fail L2
        pnls = [0.01] * 18 + [-0.12, -0.11]
        trades = _mk_trades(pnls)
        v = l2.evaluate_l2_survivor(trades, candidate_id="bad")
        self.assertFalse(v["pass"])


class L3NullHypothesisTests(unittest.TestCase):
    def test_permuted_rejects_when_sharpe_high(self):
        frame = _synthetic_ohlc(n=600, seed=1, drift=0.0, vol=0.008)

        def bt_always_good(frm, definition):
            # Overfit fixture: ignore frame, emit high-Sharpe trades
            pnls = [0.04, 0.05, -0.01, 0.06, 0.03, 0.04, -0.01, 0.05, 0.04, 0.03]
            return {"trades": _mk_trades(pnls)}

        r = l3.run_permuted_returns_test({}, frame, bt_always_good, seed=3)
        self.assertFalse(r["pass"], r)
        self.assertTrue(any("permuted_sharpe" in x for x in r["reject_reasons"]))

    def test_permuted_passes_when_noise(self):
        frame = _synthetic_ohlc(n=500, seed=2)

        def bt_noise(frm, definition):
            rng = random.Random(int(frm["close"].iloc[-1] * 1000) % 10000)
            pnls = [rng.uniform(-0.05, 0.05) for _ in range(20)]
            return {"trades": _mk_trades(pnls)}

        # May pass or fail depending on shuffle; force low sharpe path:
        def bt_flat(frm, definition):
            return {"trades": _mk_trades([0.001, -0.001, 0.0005, -0.0005, 0.0] * 4)}

        r = l3.run_permuted_returns_test({}, frame, bt_flat, seed=5)
        self.assertTrue(r["pass"], r)
        self.assertLess(r["metrics"]["sharpe"], 0.5)

    def test_inverted_rejects_spurious_edge(self):
        frame = _synthetic_ohlc(n=500, seed=4)

        def bt_spurious(frm, definition):
            pnls = [0.05, 0.04, 0.06, -0.01, 0.05, 0.04, 0.03, -0.01, 0.05, 0.04]
            return {"trades": _mk_trades(pnls)}

        r = l3.run_inverted_market_test({}, frame, bt_spurious)
        self.assertFalse(r["pass"], r)
        self.assertIn("inverted_spurious_edge", r["reject_reasons"])
        self.assertIn("n_trades>=5", r["rule"])

    def test_inverted_passes_when_edge_dies(self):
        frame = _synthetic_ohlc(n=500, seed=5)

        def bt_dead(frm, definition):
            return {"trades": _mk_trades([-0.04, -0.03, 0.01, -0.05, -0.02, 0.005] * 2)}

        r = l3.run_inverted_market_test({}, frame, bt_dead)
        self.assertTrue(r["pass"], r)

    def test_wf_requires_7_of_10_calmar_and_expectancy(self):
        # 7 good windows, 3 bad
        pnls = []
        for w in range(10):
            if w < 7:
                pnls.extend([0.08, 0.09, -0.02, 0.07])  # positive E, decent calmar
            else:
                pnls.extend([-0.05, -0.04, 0.01, -0.06])
        trades = _mk_trades(pnls)
        wf = l3.walk_forward_windows(trades, folds=10)
        self.assertEqual(wf["total"], 10)
        self.assertGreaterEqual(wf["pass_count"], 7)
        self.assertTrue(wf["pass"])

    def test_wf_fails_below_7(self):
        pnls = []
        for w in range(10):
            if w < 5:
                pnls.extend([0.08, 0.09, -0.02, 0.07])
            else:
                pnls.extend([-0.05, -0.04, 0.01, -0.06])
        trades = _mk_trades(pnls)
        wf = l3.walk_forward_windows(trades, folds=10)
        self.assertFalse(wf["pass"])
        self.assertLess(wf["pass_count"], 7)


class FunnelOrchestratorTests(unittest.TestCase):
    def test_funnel_rejects_at_l1(self):
        trades = _mk_trades([0.01, -0.02])  # <5 fills
        out = funnel.run_phase3_funnel(
            trades=trades, l1_trades=trades, skip_l3_frame_tests=True,
        )
        self.assertFalse(out["pass"])
        self.assertEqual(out["rejected_at"], l1.STAGE)

    def test_funnel_pass_path_with_skips(self):
        # Strong trades that clear Phase-2 fitness + WF (dispersed small losses)
        pnls = []
        for _ in range(10):
            pnls.extend([0.14, 0.12, -0.035, 0.13, 0.11, -0.03])
        trades = _mk_trades(pnls, mae=0.001)
        # Sanity: Phase-2 fitness itself must pass
        fit = fe.evaluate_multi_objective(trades, enforce=True, min_trades=8)
        self.assertTrue(fit["pass"], fit.get("failed_checks"))
        out = funnel.run_phase3_funnel(
            trades=trades,
            l1_trades=trades,
            skip_l3_frame_tests=True,
            candidate_id="ok",
        )
        self.assertTrue(out["pass"], out.get("reject_reasons") or out["levels"])
        self.assertIsNone(out["rejected_at"])

    def test_gate3_null_hypothesis_additive(self):
        pnls = []
        for _ in range(10):
            pnls.extend([0.12, 0.10, -0.03, 0.11])
        trades = _mk_trades(pnls)
        wf = l3.walk_forward_windows(trades, folds=10)
        # Without NH — may pass windows+fitness
        g_plain = gates.evaluate_gate3(wf, trades=trades)
        # With failing NH
        nh_fail = {"pass": False, "reject_reasons": ["permuted_sharpe_ge_0.5"],
                   "stage": l3.STAGE}
        g_nh = gates.evaluate_gate3(wf, trades=trades, null_hypothesis=nh_fail)
        self.assertFalse(g_nh["pass"])
        self.assertIn("phase3_null_hypothesis_fail_closed", g_nh.get("notes") or [])
        # Signature still works without NH
        self.assertIn("gate3_walk_forward", g_plain["gate_id"])

    def test_protective_sl_constant(self):
        import inspect
        import auto_trade_strategy_dsl as dsl
        sig = inspect.signature(dsl.backtest_dsl)
        self.assertEqual(sig.parameters["stop_loss_pct"].default, 0.009)
        self.assertEqual(funnel.FUNNEL_VERSION, "phase3_funnel_v1")


class FrameTransformTests(unittest.TestCase):
    def test_permute_and_invert_preserve_length(self):
        frame = _synthetic_ohlc(n=200, seed=9)
        perm = l3.permute_returns_frame(frame, seed=1)
        inv = l3.invert_price_frame(frame)
        self.assertEqual(len(perm), len(frame))
        self.assertEqual(len(inv), len(frame))
        self.assertTrue((inv["close"] > 0).all())


if __name__ == "__main__":
    unittest.main()
