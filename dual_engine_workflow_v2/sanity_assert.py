# -*- coding: utf-8 -*-
"""Assert-first sanity harness (TDD for strategy DSL) — Stage 2 pretest quality.

Build tiny synthetic frames with extreme boundary values. If entry/exit
semantics do not match the contract expectations, the translation is garbage:
FAIL CLOSED — do not enter additive optimize / L0 / Gate2.
"""
from __future__ import print_function


class _Col(object):
    __slots__ = ("_v",)

    def __init__(self, values):
        self._v = list(values)

    def iloc(self, index):
        # support series.iloc[index] used by DSL
        return self._v[index]

    def __getitem__(self, index):
        return self._v[index]

    def __len__(self):
        return len(self._v)


# Attach iloc as attribute-like accessor
class _ILoc(object):
    def __init__(self, col):
        self._col = col

    def __getitem__(self, index):
        if isinstance(index, slice):
            return self._col._v[index]
        return self._col._v[index]


_Col.iloc = property(lambda self: _ILoc(self))


class _Frame(object):
    """Minimal DataFrame-like for DSL evaluate_expression (no pandas required)."""

    def __init__(self, columns, n):
        self._cols = {k: _Col(v) for k, v in columns.items()}
        self._n = n
        self.columns = list(self._cols.keys())

    def __getitem__(self, key):
        return self._cols[key]

    def __contains__(self, key):
        return key in self._cols

    def __len__(self):
        return self._n

    def copy(self):
        return _Frame({k: list(c._v) for k, c in self._cols.items()}, self._n)

    def set(self, index, **kwargs):
        for k, v in kwargs.items():
            if k not in self._cols:
                self._cols[k] = _Col([0.0] * self._n)
                self.columns = list(self._cols.keys())
            self._cols[k]._v[index] = v
        return self


def _base_frame(n=60, mid=100.0):
    close = [mid] * n
    high = [mid + 0.5] * n
    low = [mid - 0.5] * n
    open_ = list(close)
    vol = [1000.0] * n
    fr = _Frame({
        "open": open_, "high": high, "low": low, "close": close, "volume": vol,
        "prev_high48": [mid + 2.0] * n,
        "prev_low48": [mid - 2.0] * n,
        "prev_mid48": [mid] * n,
        "vol_ma20_ratio": [1.0] * n,
        "atr14": [1.0] * n,
    }, n)
    return fr


def _eval_entry(dsl, frame, index):
    import auto_trade_strategy_dsl as dsl_mod
    strategy = dsl_mod.validate_strategy(dsl)
    ok, _ = dsl_mod.evaluate_expression(frame, index, strategy["entry"], explain=False)
    return bool(ok)


def _eval_exit_op_only(dsl, frame, index, position, direction):
    import auto_trade_strategy_dsl as dsl_mod
    strategy = dsl_mod.validate_strategy(dsl)
    ok, details = dsl_mod.evaluate_expression(
        frame, index, strategy["exit"], explain=True,
        position=position, direction=direction,
    )
    fired = []
    for row in details or []:
        if row.get("passed") and row.get("exit_op"):
            fired.append(str(row.get("exit_op")))
        elif row.get("passed") and row.get("condition_id"):
            fired.append("cond:%s" % row.get("condition_id"))
    return bool(ok), fired


def assert_rolling_4h_sweep_long(dsl):
    """Minimal semantic asserts for Rolling 4H Sweep Fade long."""
    failures = []
    direction = "long"
    fr = _base_frame()
    i = 50
    fr.set(
        i,
        low=97.5,
        close=98.2,
        high=98.5,
        prev_low48=98.0,
        prev_high48=102.0,
        prev_mid48=100.0,
        vol_ma20_ratio=1.20,
    )
    if not _eval_entry(dsl, fr, i):
        failures.append("ASSERT_FAIL:sweep_reclaim_mild_vol_must_enter")

    fr_b = fr.copy()
    fr_b.set(i, close=97.8)
    if _eval_entry(dsl, fr_b, i):
        failures.append("ASSERT_FAIL:no_reclaim_must_reject")

    fr_c = fr.copy()
    fr_c.set(i, vol_ma20_ratio=1.05)
    if _eval_entry(dsl, fr_c, i):
        failures.append("ASSERT_FAIL:vol_below_1_15_must_reject")

    fr_d = fr.copy()
    fr_d.set(i, close=102.5, high=103.0)
    if _eval_entry(dsl, fr_d, i):
        failures.append("ASSERT_FAIL:close_outside_box_must_reject")

    pos = {
        "price": 98.2,
        "peak_high": 98.5,
        "peak_low": 97.5,
        "entry_bar_low": 97.5,
        "entry_bar_high": 98.5,
    }
    j = i + 1
    fr_e = fr.copy()
    stop = 97.5 * (1.0 - 0.0008)
    fr_e.set(j, low=stop - 0.01, high=98.0, close=97.9, prev_mid48=100.0, atr14=1.0)
    ok, fired = _eval_exit_op_only(dsl, fr_e, j, pos, direction)
    if "entry_wick_buffer" not in fired:
        failures.append(
            "ASSERT_FAIL:entry_wick_buffer_must_fire got=%s" % ",".join(fired)
        )

    fr_f = fr.copy()
    fr_f.set(j, low=99.0, high=100.5, close=100.2, prev_mid48=100.0)
    ok_f, fired_f = _eval_exit_op_only(dsl, fr_f, j, dict(pos), direction)
    mid_ok = any("mid" in x for x in fired_f) or ok_f
    if not mid_ok:
        failures.append(
            "ASSERT_FAIL:midline_tp_must_fire got=%s" % ",".join(fired_f)
        )

    return failures


def assert_rolling_4h_sweep_short(dsl):
    failures = []
    fr = _base_frame()
    i = 50
    fr.set(
        i,
        high=102.5,
        close=101.8,
        low=101.5,
        prev_high48=102.0,
        prev_low48=98.0,
        prev_mid48=100.0,
        vol_ma20_ratio=1.20,
    )
    if not _eval_entry(dsl, fr, i):
        failures.append("ASSERT_FAIL:short_sweep_reclaim_mild_vol_must_enter")
    fr_b = fr.copy()
    fr_b.set(i, close=102.2)
    if _eval_entry(dsl, fr_b, i):
        failures.append("ASSERT_FAIL:short_no_reclaim_must_reject")
    return failures


def _ny_base_frame(n=60, mid=100.0):
    fr = _base_frame(n=n, mid=mid)
    # Default outside NY window / flat london box
    for i in range(n):
        fr.set(
            i,
            hour_utc=10.0,
            london_high=mid + 2.0,
            london_low=mid - 2.0,
            london_mid=mid,
            vwap=mid,
            vol_ma20_ratio=1.0,
        )
    return fr


def assert_ny_open_liq_fade_long(dsl):
    """Sweep/reclaim/wick/VWAP partial/ATR trail asserts for NY Open fade long."""
    failures = []
    direction = "long"
    fr = _ny_base_frame()
    i = 50
    fr.set(
        i,
        hour_utc=13.0,          # inside 12:30–15:30
        low=97.5,
        close=98.2,
        high=98.5,
        london_low=98.0,
        london_high=102.0,
        vwap=100.0,
        vol_ma20_ratio=1.25,
    )
    if not _eval_entry(dsl, fr, i):
        failures.append("ASSERT_FAIL:ny_sweep_reclaim_vol_must_enter")

    fr_time = fr.copy()
    fr_time.set(i, hour_utc=11.0)  # before NY window
    if _eval_entry(dsl, fr_time, i):
        failures.append("ASSERT_FAIL:outside_ny_window_must_reject")

    fr_b = fr.copy()
    fr_b.set(i, close=97.8)
    if _eval_entry(dsl, fr_b, i):
        failures.append("ASSERT_FAIL:no_reclaim_must_reject")

    fr_c = fr.copy()
    fr_c.set(i, vol_ma20_ratio=1.10)
    if _eval_entry(dsl, fr_c, i):
        failures.append("ASSERT_FAIL:vol_below_1_2_must_reject")

    pos = {
        "price": 98.2,
        "peak_high": 98.5,
        "peak_low": 97.5,
        "entry_bar_low": 97.5,
        "entry_bar_high": 98.5,
    }
    j = i + 1
    fr_e = fr.copy()
    stop = 97.5 * (1.0 - 0.0008)
    fr_e.set(j, low=stop - 0.01, high=98.0, close=97.9, vwap=100.0, atr14=1.0)
    ok, fired = _eval_exit_op_only(dsl, fr_e, j, pos, direction)
    if "entry_wick_buffer" not in fired:
        failures.append(
            "ASSERT_FAIL:entry_wick_buffer_must_fire got=%s" % ",".join(fired)
        )

    # VWAP partial TP: close reaches vwap from below
    fr_v = fr.copy()
    fr_v.set(j, low=99.0, high=100.5, close=100.2, vwap=100.0, atr14=1.0)
    ok_v, fired_v = _eval_exit_op_only(dsl, fr_v, j, dict(pos), direction)
    if "partial_tp_feature" not in fired_v:
        failures.append(
            "ASSERT_FAIL:vwap_partial_tp_must_fire got=%s" % ",".join(fired_v)
        )

    # ATR trail must be present as an exit_op leaf (fire when trail hit)
    fr_t = fr.copy()
    # After large adverse move from peak — set peak_high high then crush low
    pos_t = dict(pos)
    pos_t["peak_high"] = 110.0
    pos_t["partial_taken"] = True  # remaining size trails
    fr_t.set(j, low=90.0, high=91.0, close=90.5, vwap=100.0, atr14=1.0)
    ok_t, fired_t = _eval_exit_op_only(dsl, fr_t, j, pos_t, direction)
    if "atr_trailing" not in fired_t:
        failures.append(
            "ASSERT_FAIL:atr_trailing_must_fire got=%s" % ",".join(fired_t)
        )

    return failures


def assert_ny_open_liq_fade_short(dsl):
    failures = []
    fr = _ny_base_frame()
    i = 50
    fr.set(
        i,
        hour_utc=14.0,
        high=102.5,
        close=101.8,
        low=101.5,
        london_high=102.0,
        london_low=98.0,
        vwap=100.0,
        vol_ma20_ratio=1.25,
    )
    if not _eval_entry(dsl, fr, i):
        failures.append("ASSERT_FAIL:short_ny_sweep_reclaim_vol_must_enter")
    fr_b = fr.copy()
    fr_b.set(i, close=102.2)
    if _eval_entry(dsl, fr_b, i):
        failures.append("ASSERT_FAIL:short_no_reclaim_must_reject")
    fr_t = fr.copy()
    fr_t.set(i, hour_utc=16.0)
    if _eval_entry(dsl, fr_t, i):
        failures.append("ASSERT_FAIL:short_outside_ny_window_must_reject")
    return failures


ASSERT_REGISTRY = {
    "rolling_4h_sweep_5m_v1": {
        "long": assert_rolling_4h_sweep_long,
        "short": assert_rolling_4h_sweep_short,
    },
    "rolling_4h_sweep_5m": {
        "long": assert_rolling_4h_sweep_long,
        "short": assert_rolling_4h_sweep_short,
    },
    "ny_open_liq_fade_v1": {
        "long": assert_ny_open_liq_fade_long,
        "short": assert_ny_open_liq_fade_short,
    },
    "ny_open_liq_fade_clean_v1": {
        "long": assert_ny_open_liq_fade_long,
        "short": assert_ny_open_liq_fade_short,
    },
}


def _family_key(pack):
    spec = (pack or {}).get("mechanism_spec") or {}
    fam = str(spec.get("mechanism_family") or spec.get("mechanism_name") or "")
    if "_ad" in fam:
        fam = fam.rsplit("_ad", 1)[0]
    return fam


def run_sanity_asserts(pack, direction=None, contract=None):
    """Run registered / contract-declared asserts. Fail-closed."""
    import auto_trade_strategy_dsl as dsl_mod

    direction = direction or ((pack.get("meta") or {}).get("direction") or "long")
    dsl = pack.get("dsl_%s" % direction) or pack.get("dsl")
    if not isinstance(dsl, dict):
        return {
            "pass": False,
            "failures": ["NO_DSL"],
            "stage": "sanity_assert",
            "quality": "SHIT_TRANSLATION",
        }
    try:
        dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        return {
            "pass": False,
            "failures": ["DSL_INVALID:%s" % exc],
            "stage": "sanity_assert",
            "quality": "SHIT_TRANSLATION",
        }

    fam = _family_key(pack)
    if contract and contract.get("assert_suite"):
        fam = contract.get("assert_suite") or fam
    suite = ASSERT_REGISTRY.get(fam) or {}
    fn = suite.get(direction)
    if fn is None:
        require = bool((contract or {}).get("require_sanity_asserts", True))
        if require:
            return {
                "pass": False,
                "failures": ["ASSERT_SUITE_MISSING:%s:%s" % (fam, direction)],
                "stage": "sanity_assert",
                "quality": "SHIT_TRANSLATION",
            }
        return {
            "pass": True,
            "failures": [],
            "stage": "sanity_assert",
            "skipped": True,
            "quality": "ok",
        }

    failures = list(fn(dsl) or [])
    return {
        "pass": not failures,
        "failures": failures,
        "stage": "sanity_assert",
        "family": fam,
        "direction": direction,
        "quality": "ok" if not failures else "SHIT_TRANSLATION",
    }
