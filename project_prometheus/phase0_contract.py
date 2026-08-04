# -*- coding: utf-8 -*-
"""Phase 0 contract floors for mechanism EXISTENCE (not Formal PASS)."""
from __future__ import print_function

# Path event geometry (immutable price distances)
LEVERAGE = 20
STOP_PRICE_PCT = 0.005
TARGET_PRICE_PCT = 0.005555

# Existence floors (Phase 0 acceptance — as specified)
EXISTENCE_WIN_RATE_MIN = 0.70          # path profit_first rate among resolved
EXISTENCE_MEAN_WIN_LEVERED_NET_MIN = 0.1111
EXISTENCE_WEEKLY_FREQ_MIN = 0.10
EXISTENCE_MIN_RESOLVED = 40            # reproducibility floor
EXISTENCE_MIN_TEMPORAL_POSITIVE = 0.60  # ≥60% of time slices have PFR≥0.5

# Fee model (from production execution_cost_model.json — taker Lv1)
FEE_RATE_PER_SIDE = 0.0005
SLIP_RATE_PER_SIDE = 0.0002
ROUND_TRIP_PRICE_COST = 2.0 * (FEE_RATE_PER_SIDE + SLIP_RATE_PER_SIDE)  # 0.0014
LEVERED_FEE_DRAG = ROUND_TRIP_PRICE_COST * LEVERAGE  # 0.028

# Gross target touch levered = 0.1111; net after fee drag ≈ 0.0831
TARGET_LEVERED_GROSS = TARGET_PRICE_PCT * LEVERAGE
TARGET_LEVERED_NET = TARGET_LEVERED_GROSS - LEVERED_FEE_DRAG

# For net mean-win ≥ 0.1111, winning paths need mean MFE beyond target:
REQUIRED_MEAN_WIN_MOVE_PCT = (EXISTENCE_MEAN_WIN_LEVERED_NET_MIN + LEVERED_FEE_DRAG) / float(LEVERAGE)

HORIZONS = (12, 24, 48, 96)
STRIDE_DEFAULT = 2

CONTRACT_NOTE = {
    "phase": "Prometheus Phase 0 — Existence",
    "not_success_criteria": [
        "Formal PASS",
        "Discovery PASS",
        "Builder PASS",
        "Strategy creation",
    ],
    "existence_questions": [
        "Does any market-state region yield path profit_first_rate >= 0.70?",
        "With mean winning levered NET return >= 0.1111 after fees?",
        "And weekly event frequency >= 0.10?",
        "Reproducible (min resolved, temporal stability)?",
    ],
    "fee_drag_levered": LEVERED_FEE_DRAG,
    "target_levered_gross": TARGET_LEVERED_GROSS,
    "target_levered_net_if_exit_at_target": TARGET_LEVERED_NET,
    "required_mean_win_move_pct_for_net_1111": REQUIRED_MEAN_WIN_MOVE_PCT,
}
