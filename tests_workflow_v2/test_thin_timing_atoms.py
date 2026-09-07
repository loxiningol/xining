# -*- coding: utf-8 -*-
from __future__ import print_function

from dual_engine_workflow_v2 import thin_timing_atoms as tta


def test_default_timing_locked():
    rows = tta.default_timing()
    assert len(rows) == 2
    assert rows[0]["factor"] == "skdj_diff"
    assert rows[0]["operator"] == "above"
    assert rows[0]["value"] == -20
    assert rows[1]["factor"] == "macd_hist"
    assert "cross_up" not in str(rows)


def test_timing_ok_and_alias():
    ok = [
        {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
        {"factor": "macd_hist", "operator": "above", "value": -1},
    ]
    assert tta.timing_ok(ok) is True
    aliased = tta.normalize_timing([
        {"factor": "skdj_k", "operator": "above", "value": 50, "window": 9},
        {"factor": "cci_oversold", "operator": "below", "value": -100},
    ])
    assert aliased is not None
    assert aliased[1]["factor"] == "cci"


def test_diversity_same_level_group_blocked():
    a = [{"factor": "skdj_k", "operator": "above", "value": 40}]
    b = [{"factor": "skdj_d", "operator": "below", "value": 60}]
    c = [{"factor": "macd_hist", "operator": "above", "value": 0}]
    counts, warn = tta.diversity_counts([a, b], c)
    assert counts is False
    assert "探索深度" in warn


def test_diversity_cross_vs_level_ok():
    a = [{"factor": "skdj_k", "operator": "above", "value": 40}]
    b = [{"factor": "skdj_d", "operator": "below", "value": 60}]
    c = [{"factor": "skdj_kd", "operator": "cross_up", "value": 0}]
    counts, warn = tta.diversity_counts([a, b], c)
    assert counts is True
    assert warn == ""
