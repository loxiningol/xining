# Phase0 Region → Strategy：样本外复现报告

- at: 2026-08-04 23:12:57
- WR floor: **0.65** · mean_win_net: **0.1111** · weekly: **0.10**

## Region A — **PHASE0_FALSE_POSITIVE**

- may_strategize: `False`
- strict_oos_pass: `False` · recent_2y_pass: `False` · rolling: 2/4
- data_span_days: 41.66319444444444 · n_region_signals_full: 593

| split | wr | mean_win_net | weekly | n | gates_ok | fails |
|---|---|---|---|---|---|---|
| full_sample | 0.665 | 0.1267 | 103.19 | 576 | True |  |
| train | 0.612 | 0.1329 | 100.73 | 291 | False | win_rate_lt_0.65 |
| validation | 0.92 | 0.133 | 167.3 | 100 | True |  |
| test | 0.75 | 0.119 | 130.84 | 76 | True |  |
| holdout_tail | 0.514 | 0.1046 | 105.7 | 109 | False | win_rate_lt_0.65,mean_win_net_lt_0.1111 |
| recent_2y | 0.665 | 0.1267 | 103.19 | 576 | False | recent_2y_data_span_lt_600d(got=41.7) |
| rolling_oos_1 | 0.92 | 0.133 | 167.3 | 100 | True |  |
| rolling_oos_2 | 0.962 | 0.1191 | 268.29 | 53 | True |  |
| rolling_oos_3 | 0.446 | 0.1067 | 103.31 | 74 | False | win_rate_lt_0.65,mean_win_net_lt_0.1111 |
| rolling_oos_4 | 0.5 | 0.105 | 97.28 | 58 | False | win_rate_lt_0.65,mean_win_net_lt_0.1111 |

## Region B — **PHASE0_FALSE_POSITIVE**

- may_strategize: `False`
- strict_oos_pass: `False` · recent_2y_pass: `False` · rolling: 1/4
- data_span_days: 41.66319444444444 · n_region_signals_full: 414

| split | wr | mean_win_net | weekly | n | gates_ok | fails |
|---|---|---|---|---|---|---|
| full_sample | 0.642 | 0.1225 | 73.95 | 165 | False | win_rate_lt_0.65 |
| train | 0.784 | 0.1225 | 70.46 | 74 | True |  |
| validation | 0.743 | 0.1164 | 105.28 | 35 | True |  |
| test | 0.143 | 0.0967 | 124.84 | 7 | False | trade_count_lt_15,win_rate_lt_0.65,mean_win_net_lt_0.1111 |
| holdout_tail | 0.429 | 0.1312 | 72.76 | 49 | False | win_rate_lt_0.65 |
| recent_2y | 0.642 | 0.1225 | 73.95 | 165 | False | win_rate_lt_0.65,recent_2y_data_span_lt_600d(got=41.7) |
| rolling_oos_1 | 0.69 | 0.1201 | 95.42 | 29 | True |  |
| rolling_oos_2 | 0.5 | 0.1041 | 65.91 | 12 | False | win_rate_lt_0.65,mean_win_net_lt_0.1111 |
| rolling_oos_3 | 0.611 | 0.128 | 114.94 | 18 | False | win_rate_lt_0.65 |
| rolling_oos_4 | 0.344 | 0.1314 | 102.13 | 32 | False | win_rate_lt_0.65 |
