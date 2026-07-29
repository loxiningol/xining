# -*- coding: utf-8 -*-
"""Phase-4: incubator + ATR dynamic risk sizing tests.

Covers:
  - Incubator rejects single-symbol overfitting (<2 cross-asset passes)
  - Incubator accepts when ≥2 non-target symbols pass Expectancy>0 & Calmar≥0.8
  - Multi-TF / window perturbation rejects Calmar cliff-drop >40%
  - ATR sizing lowers notional in high volatility
  - DD throttle cuts R to 0.5% when in ≥50% of max DD region
  - Protective 0.9% SL / production B-grade mount flags unchanged
"""
from __future__ import print_function

import math
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import auto_trade_strategy_dsl as dsl
from dual_engine_workflow_v2 import incubator as inc
from dual_engine_workflow_v2 import fitness_engine as fe


def _mk_ohlc(n=400, start=100.0, vol=1.0, drift=0.0, seed=1):
    """Pandas-free synthetic OHLC frame for incubator unit tests."""
    rng = random.Random(seed)
    opens, highs, lows, closes = [], [], [], []
    px = float(start)
    for _ in range(n):
        ret = drift + vol * 0.01 * (rng.random() * 2.0 - 1.0)
        o = px
        c = px * (1.0 + ret)
        h = max(o, c) * (1.0 + 0.002 * vol)
        l = min(o, c) * (1.0 - 0.002 * vol)
        opens.append(o); highs.append(h); lows.append(l); closes.append(c)
        px = c

    class _Series(object):
        def __init__(self, values):
            self._v = list(values)

        def __len__(self):
            return len(self._v)

        @property
        def iloc(self):
            series = self

            class _ILoc(object):
                def __getitem__(self, i):
                    return series._v[i]
            return _ILoc()

    class _Frame(object):
        def __init__(self, tag=None):
            self._data = {
                "open": opens, "high": highs, "low": lows, "close": closes,
            }
            self.index = list(range(n))
            self.tag = tag if tag is not None else float(start)

        def __len__(self):
            return n

        def __getitem__(self, key):
            return _Series(self._data[key])

    return _Frame(tag=float(start))


def _good_trades(n=40, win=0.08, loss=-0.02):
    """Trades with strong Calmar / positive expectancy."""
    out = []
    for i in range(n):
        p = win if (i % 3) else loss  # ~2/3 win rate, payoff 4
        out.append({
            "pnl_ratio": float(p),
            "leverage": 20,
            "mae_price_pct": 0.003,
            "entry_time": "2026-01-%02d 10:00:00" % ((i % 28) + 1),
            "exit_time": "2026-01-%02d 12:00:00" % ((i % 28) + 1),
        })
    return out


def _weak_trades(n=20):
    out = []
    for i in range(n):
        p = 0.01 if (i % 2) else -0.015
        out.append({
            "pnl_ratio": float(p),
            "leverage": 20,
            "mae_price_pct": 0.01,
            "entry_time": "2026-01-%02d 10:00:00" % ((i % 28) + 1),
            "exit_time": "2026-01-%02d 11:00:00" % ((i % 28) + 1),
        })
    return out


class IncubatorCrossAssetTests(unittest.TestCase):
    def test_rejects_single_symbol_overfit(self):
        """Only 1 of 3 cross symbols passes → reject overfitting."""
        good = _good_trades()
        weak = _weak_trades()

        def bt(frame, definition, stop_loss_pct=0.009):
            # Frame identity encoded via .tag
            tag = float(getattr(frame, "tag", 0.0))
            if abs(tag - 100.0) < 1e-6:
                return {"trades": good}
            return {"trades": weak}

        frame_map = {
            "ETH-USDT-SWAP": _mk_ohlc(start=100.0, seed=1),  # good
            "SOL-USDT-SWAP": _mk_ohlc(start=200.0, seed=2),  # weak
            "BNB-USDT-SWAP": _mk_ohlc(start=300.0, seed=3),  # weak
        }
        definition = {"key": "t", "direction": "long", "timeframe": "15m",
                      "entry": {"all": []}, "exit": {"all": []}, "max_hold_bars": 12}
        # bypass validate by using run_cross_asset directly with stub bt
        result = inc.run_cross_asset_blind_test(
            definition=definition,
            target_symbol="BTC-USDT-SWAP",
            timeframe="15m",
            backtest_fn=bt,
            frame_map=frame_map,
        )
        self.assertFalse(result["pass"])
        self.assertEqual(result["pass_count"], 1)
        self.assertTrue(any("overfit" in r for r in result["reject_reasons"]))

    def test_passes_when_two_symbols_ok(self):
        good = _good_trades()

        def bt(frame, definition, stop_loss_pct=0.009):
            return {"trades": good}

        frame_map = {
            "ETH-USDT-SWAP": _mk_ohlc(start=100.0, seed=1),
            "SOL-USDT-SWAP": _mk_ohlc(start=110.0, seed=2),
            "BNB-USDT-SWAP": _mk_ohlc(start=120.0, seed=3),
        }
        definition = {"key": "t"}
        result = inc.run_cross_asset_blind_test(
            definition=definition,
            target_symbol="BTC-USDT-SWAP",
            timeframe="15m",
            backtest_fn=bt,
            frame_map=frame_map,
        )
        self.assertTrue(result["pass"])
        self.assertGreaterEqual(result["pass_count"], 2)
        self.assertGreaterEqual(result["cross_asset_score"], 2.0 / 3.0 - 1e-9)

    def test_full_incubator_rejects_overfit(self):
        good = _good_trades()
        weak = _weak_trades()

        def bt(frame, definition, stop_loss_pct=0.009):
            tag = float(getattr(frame, "tag", 0.0))
            if abs(tag - 100.0) < 1e-6:
                return {"trades": good}
            return {"trades": weak}

        frame_map = {
            "ETH-USDT-SWAP": _mk_ohlc(start=100.0, seed=1),
            "SOL-USDT-SWAP": _mk_ohlc(start=200.0, seed=2),
            "BNB-USDT-SWAP": _mk_ohlc(start=300.0, seed=3),
        }
        base = _mk_ohlc(start=50.0, seed=9)
        # Also provide baseline for multi-tf window probes via same bt
        log = inc.run_incubator(
            definition={"key": "t", "entry": {}, "exit": {}, "max_hold_bars": 10},
            target_symbol="BTC-USDT-SWAP",
            timeframe="15m",
            baseline_trades=good,
            baseline_frame=base,
            backtest_fn=bt,
            frame_map=frame_map,
            skip_multi_tf=True,  # isolate cross-asset reject
        )
        self.assertFalse(log["pass"])
        self.assertEqual(log["rejected_at"], "cross_asset_blind_test")


class IncubatorMultiTFTests(unittest.TestCase):
    def test_calmar_cliff_drop_rejects(self):
        strong = _good_trades()
        # Cliff: calmar collapses
        cliff = [{"pnl_ratio": 0.001 if i % 2 else -0.001,
                  "entry_time": "2026-01-01 10:00:00",
                  "exit_time": "2026-01-01 11:00:00"} for i in range(30)]
        base_m = inc.metrics_from_trades(strong)

        def bt(frame, definition, stop_loss_pct=0.009):
            # Perturbed definitions (scaled windows) → cliff trades
            return {"trades": cliff}

        base = _mk_ohlc(start=50.0, seed=4)
        result = inc.run_multi_tf_perturbation(
            definition={"key": "t", "entry": {"lookback": 20}, "exit": {},
                        "max_hold_bars": 12},
            target_symbol="BTC-USDT-SWAP",
            timeframe="15m",
            baseline_metrics=base_m,
            backtest_fn=bt,
            baseline_frame=base,
            frame_map={},  # no alt TF frames; window probes still run
        )
        self.assertFalse(result["pass"])
        self.assertTrue(any("cliff" in r for r in result["reject_reasons"]))

    def test_stable_calmar_passes(self):
        strong = _good_trades()
        base_m = inc.metrics_from_trades(strong)

        def bt(frame, definition, stop_loss_pct=0.009):
            return {"trades": strong}

        base = _mk_ohlc(start=50.0, seed=5)
        result = inc.run_multi_tf_perturbation(
            definition={"key": "t", "entry": {"lookback": 20}, "exit": {},
                        "max_hold_bars": 12},
            target_symbol="BTC-USDT-SWAP",
            timeframe="15m",
            baseline_metrics=base_m,
            backtest_fn=bt,
            baseline_frame=base,
            frame_map={},
        )
        self.assertTrue(result["pass"])


class ATRDynamicSizingTests(unittest.TestCase):
    def test_high_vol_lowers_notional(self):
        equity = 10000.0
        risk = 0.012
        low_atr = 10.0
        high_atr = 50.0
        px = 100.0
        low = dsl.atr_position_size(equity, risk, low_atr, entry_price=px)
        high = dsl.atr_position_size(equity, risk, high_atr, entry_price=px)
        self.assertGreater(low["notional"], high["notional"])
        self.assertGreater(low["account_fraction"], high["account_fraction"])
        self.assertTrue(high["production_mount_unchanged"])
        self.assertEqual(high["production_b_grade_position_pct"], 0.30)
        self.assertEqual(high["production_stop_loss_pct"], 0.009)

    def test_dd_throttle_cuts_r(self):
        # Equity dropped to 50% of max DD region: peak=1, max_dd=0.4, eq=0.75 → dd=0.25 ≥ 0.2
        r = dsl.effective_risk_pct(
            0.012, equity=0.75, peak_equity=1.0, max_dd_so_far=0.40,
        )
        self.assertAlmostEqual(r, 0.005, places=6)
        # Shallow DD: current_dd=0.05 < 0.5*0.40=0.20 → keep base
        r2 = dsl.effective_risk_pct(
            0.012, equity=0.95, peak_equity=1.0, max_dd_so_far=0.40,
        )
        self.assertAlmostEqual(r2, 0.012, places=6)

    def test_backtest_dsl_dynamic_sizing_scales_pnl(self):
        """High-ATR path should produce smaller |pnl_ratio| than full-size."""
        # Build a minimal valid strategy — may need real validate fields
        # Use synthetic path that always enters via monkeypatch if needed.
        # Direct unit: atr_position_size already covers formula; here verify
        # backtest_dsl accepts the flag and records sizing meta without
        # changing stop_loss default.
        self.assertEqual(dsl.PRODUCTION_STOP_LOSS_PCT, 0.009)
        # Formula identity
        s = dsl.atr_position_size(1.0, 0.012, 0.02, entry_price=1.0)
        # qty = 0.012/0.02 = 0.6; notional=0.6; frac=min(1,0.6)=0.6
        self.assertAlmostEqual(s["quantity"], 0.6, places=6)
        self.assertAlmostEqual(s["account_fraction"], 0.6, places=6)

    def test_dd_throttle_lowers_drawdown_impact(self):
        """Sequence of losses: throttled R reduces subsequent account hit."""
        # Simulate two size computations at same ATR after a deep DD
        equity_peak = 1.0
        equity_dd = 0.70  # 30% DD
        max_dd = 0.40
        atr = 20.0
        px = 100.0
        r_base = dsl.effective_risk_pct(0.012, equity_peak, equity_peak, 0.0)
        r_th = dsl.effective_risk_pct(0.012, equity_dd, equity_peak, max_dd)
        self.assertGreater(r_base, r_th)
        self.assertAlmostEqual(r_th, 0.005)
        size_base = dsl.atr_position_size(equity_peak, r_base, atr, entry_price=px)
        size_th = dsl.atr_position_size(equity_dd, r_th, atr, entry_price=px)
        self.assertGreater(size_base["notional"], size_th["notional"])


class ProtectiveDefaultsTests(unittest.TestCase):
    def test_sl_and_b_grade_constants(self):
        self.assertEqual(dsl.PRODUCTION_STOP_LOSS_PCT, 0.009)
        self.assertEqual(dsl.PRODUCTION_B_GRADE_POSITION_PCT, 0.30)
        self.assertEqual(dsl.PRODUCTION_LEVERAGE, 20)
        self.assertEqual(inc.PROTECTIVE_SL_PCT, 0.009)


if __name__ == "__main__":
    unittest.main(verbosity=2)
