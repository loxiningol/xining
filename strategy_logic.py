from common import ema, cci, kdj, macd

def _get_state(idx, o, c, h, l, kwargs):
    res = {}
    for name, span in [('e6',6), ('e7',7), ('e8',8), ('e21',21), ('e23',23), ('e32',32), ('e38',38), ('e53',53)]:
        arr = kwargs.get(f'{name}_arr')
        res[name] = arr[idx] if arr is not None else ema(c[:idx+1], span)
        res[name+'_m1'] = arr[idx-1] if arr is not None else ema(c[:idx], span)
    
    cci_arr = kwargs.get('cci_arr')
    res['cci'] = cci_arr[idx] if cci_arr is not None else cci(h[:idx+1], l[:idx+1], c[:idx+1])
    res['cci_m1'] = cci_arr[idx-1] if cci_arr is not None else cci(h[:idx], l[:idx], c[:idx])

    k_arr, d_arr, j_arr = kwargs.get('k_arr'), kwargs.get('d_arr'), kwargs.get('j_arr')
    if k_arr is not None and d_arr is not None and j_arr is not None:
        res['k'], res['d'], res['j'] = k_arr[idx], d_arr[idx], j_arr[idx]
        res['k_m1'], res['d_m1'], res['j_m1'] = k_arr[idx-1], d_arr[idx-1], j_arr[idx-1]
    else:
        res['k'], res['d'], res['j'] = kdj(h[:idx+1], l[:idx+1], c[:idx+1])
        res['k_m1'], res['d_m1'], res['j_m1'] = kdj(h[:idx], l[:idx], c[:idx])

    stick_arr = kwargs.get('stick_arr')
    res['stick'] = stick_arr[idx] if stick_arr is not None else macd(c[:idx+1])[2]
    res['stick_m1'] = stick_arr[idx-1] if stick_arr is not None else macd(c[:idx])[2]
    return res

def _no_ema_cross_10(idx, kwargs, c):
    e7_arr, e23_arr = kwargs.get('e7_arr'), kwargs.get('e23_arr')
    for i in range(idx - 10, idx):
        if i <= 0: continue
        v1 = e7_arr[i] > e23_arr[i] if e7_arr is not None else ema(c[:i+1], 7) > ema(c[:i+1], 23)
        v2 = e7_arr[i-1] > e23_arr[i-1] if e7_arr is not None else ema(c[:i], 7) > ema(c[:i], 23)
        if v1 != v2: return False
    return True

# --- 正式策略组 ---
def entry_cci_75_100_long(o, c, h, l, idx, **kwargs):
    if idx < 60 or c[idx] <= o[idx]: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if not (l[idx] <= s['e7'] <= h[idx] and s['e7'] > s['e23'] > s['e38'] and s['k'] > s['d'] and s['stick'] > 0): return False, {}
    if not (l[idx] > s['e38'] and l[idx-1] > s['e38_m1'] and 80 <= s['cci'] <= 90 and 0.5 <= s['k'] - s['d'] <= 5 and s['stick'] > s['stick_m1']): return False, {}
    return True, {"price": c[idx]}

def exit_cci_75_100_long(c, h, l, idx, entry, **kwargs):
    return _get_state(idx, [], c, h, l, kwargs)['cci'] >= 104

def entry_cci75_110_short(o, c, h, l, idx, **kwargs):
    if idx < 60 or c[idx] >= o[idx]: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if not (l[idx] <= s['e7'] <= h[idx] and s['e7'] > s['e23'] > s['e38'] and s['k'] > s['d'] and s['stick'] > 0): return False, {}
    if not (l[idx] > s['e38'] and l[idx-1] > s['e38_m1'] and 75 <= s['cci'] <= 110 and 55 <= s['k'] <= 70 and s['stick'] > s['stick_m1']): return False, {}
    return True, {"price": c[idx]}

def exit_cci75_110_short(c, h, l, idx, entry, **kwargs):
    s = _get_state(idx, [], c, h, l, kwargs)
    return s['k'] < 53 or s['j'] < 47

# --- 实验策略组 (14条完整实现) ---
def entry_ema7_break_long(o, c, h, l, idx, **kwargs):
    if idx < 60: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (s['e32'] < s['e23'] < s['e8'] < s['e7']) and (o[idx] < s['e7'] and c[idx] > s['e7']) and (40 <= s['cci'] <= 110): return True, {"price": c[idx]}
    return False, {}

def exit_ema7_break_long(c, h, l, idx, entry, **kwargs):
    s = _get_state(idx, [], c, h, l, kwargs)
    return s['k'] >= 86.5 or s['cci'] > 135

def entry_ema7_break_short(o, c, h, l, idx, **kwargs):
    if idx < 60: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (s['e7'] < s['e8'] < s['e23'] < s['e32']) and (o[idx] > s['e7'] and c[idx] < s['e7']) and (-110 <= s['cci'] <= -40): return True, {"price": c[idx]}
    return False, {}

def exit_ema7_break_short(c, h, l, idx, entry, **kwargs):
    s = _get_state(idx, [], c, h, l, kwargs)
    return s['k'] <= 13.5 or s['cci'] < -135

def entry_ema8_mainwave_long(o, c, h, l, idx, **kwargs):
    if idx < 60: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (s['e32'] > s['e7'] > s['e8']) and (45 <= s['k'] <= 70 and s['d'] < 70 and s['j'] < 85) and s['stick'] > 0 and (s['cci_m1'] <= -60 and s['cci'] > -60): return True, {"price": c[idx]}
    return False, {}

def exit_ema8_mainwave_long(c, h, l, idx, entry, **kwargs):
    s = _get_state(idx, [], c, h, l, kwargs)
    return (s['k'] > 78 and s['j'] > 78) or s['j'] > 105

def entry_ema8_mainwave_short(o, c, h, l, idx, **kwargs):
    if idx < 60: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (s['e32'] < s['e7'] < s['e8']) and (30 <= s['k'] <= 55 and s['d'] > 30 and s['j'] > 15) and s['stick'] < 0 and (s['cci_m1'] >= 60 and s['cci'] < 60): return True, {"price": c[idx]}
    return False, {}

def exit_ema8_mainwave_short(c, h, l, idx, entry, **kwargs):
    s = _get_state(idx, [], c, h, l, kwargs)
    return s['k'] < 22 and s['j'] < 22

def entry_ema7_cross_long(o, c, h, l, idx, **kwargs):
    if idx < 60: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (s['e7'] > s['e23'] > s['e32']) and (min(s['e8'], s['e32']) <= c[idx] <= max(s['e8'], s['e32'])) and (c[idx] > o[idx] and o[idx] < s['e7'] and c[idx] > s['e7']) and (21 <= s['j'] <= 50 and -110 <= s['cci'] <= -50): return True, {"price": c[idx]}
    return False, {}

def exit_ema7_cross_long(c, h, l, idx, entry, **kwargs):
    return _get_state(idx, [], c, h, l, kwargs)['j'] > 50

def entry_cci_110_135_long(o, c, h, l, idx, **kwargs):
    if idx < 60 or c[idx] <= o[idx]: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (l[idx] <= s['e7'] <= h[idx]) and (s['e7'] > s['e23'] > s['e38']) and (s['k'] > s['d'] and s['stick'] > 0) and (110 <= s['cci'] <= 135) and _no_ema_cross_10(idx, kwargs, c): return True, {"price": c[idx]}
    return False, {}

def exit_cci_110_135_long(c, h, l, idx, entry, **kwargs):
    return _get_state(idx, [], c, h, l, kwargs)['cci'] > 145

def entry_j_cross_79_short(o, c, h, l, idx, **kwargs):
    if idx < 60 or c[idx] >= o[idx]: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (l[idx] <= s['e7'] <= h[idx]) and (s['e7'] > s['e23'] > s['e38']) and (s['k'] > s['d'] and s['k'] < 85) and (s['j_m1'] >= 79 and s['j'] < 79) and _no_ema_cross_10(idx, kwargs, c): return True, {"price": c[idx]}
    return False, {}

def exit_j_cross_79_short(c, h, l, idx, entry, **kwargs):
    return _get_state(idx, [], c, h, l, kwargs)['j'] <= 56

def entry_cci75_110_long(o, c, h, l, idx, **kwargs):
    if idx < 60 or c[idx] <= o[idx]: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (l[idx] <= s['e7'] <= h[idx]) and (-110 <= s['cci'] <= -75) and (30 < s['k'] < 45 and s['k'] > s['d']) and (s['stick'] > 0 and s['stick'] > s['stick_m1']) and (s['e7'] < s['e23'] < s['e38']) and _no_ema_cross_10(idx, kwargs, c): return True, {"price": c[idx]}
    return False, {}

def exit_cci75_110_long(c, h, l, idx, entry, **kwargs):
    s = _get_state(idx, [], c, h, l, kwargs)
    return s['k'] > 47 or s['j'] > 53

def entry_j_cross_7_8_long(o, c, h, l, idx, **kwargs):
    if idx < 60: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (-250 <= s['cci'] <= -100) and (s['j_m1'] < 7.8 and s['j'] >= 7.8) and (s['d'] < 20 and s['k'] < s['d']) and (s['e7'] < s['e23'] < s['e38']) and _no_ema_cross_10(idx, kwargs, c): return True, {"price": c[idx]}
    return False, {}

def exit_j_cross_7_8_long(c, h, l, idx, entry, **kwargs):
    return _get_state(idx, [], c, h, l, kwargs)['j'] >= 25

def entry_cci_less_neg170_long(o, c, h, l, idx, **kwargs):
    if idx < 60 or c[idx] <= o[idx]: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (s['cci'] < -170) and (s['k_m1'] < 15 and s['k'] >= 15) and (s['k'] < s['d'] and c[idx] < o[idx-1]) and _no_ema_cross_10(idx, kwargs, c): return True, {"price": c[idx]}
    return False, {}

def exit_cci_less_neg170_long(c, h, l, idx, entry, **kwargs):
    return _get_state(idx, [], c, h, l, kwargs)['j'] >= 50

def entry_cci_neg60_neg110_short(o, c, h, l, idx, **kwargs):
    if idx < 60 or c[idx] >= o[idx]: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (l[idx] <= s['e7'] <= h[idx]) and (s['e7'] < s['e23'] < s['e38']) and (s['d'] < 40 and s['k'] <= s['d'] and s['d'] - s['k'] <= 2) and (-110 <= s['cci'] <= -60) and _no_ema_cross_10(idx, kwargs, c): return True, {"price": c[idx]}
    return False, {}

def exit_cci_neg60_neg110_short(c, h, l, idx, entry, **kwargs):
    return _get_state(idx, [], c, h, l, kwargs)['j'] <= 18

def entry_cci_neg110_neg180_long(o, c, h, l, idx, **kwargs):
    if idx < 60: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (c[idx] <= s['e7']) and (s['k'] <= s['d'] and s['k'] < 30 and s['d'] < 30 and s['j'] < 30) and (-180 <= s['cci'] <= -110) and (s['cci'] > s['cci_m1'] and s['k'] > s['k_m1'] and s['d'] > s['d_m1'] and s['j'] > s['j_m1']) and (s['e7'] < s['e23'] < s['e38']) and _no_ema_cross_10(idx, kwargs, c): return True, {"price": c[idx]}
    return False, {}

def exit_cci_neg110_neg180_long(c, h, l, idx, entry, **kwargs):
    return _get_state(idx, [], c, h, l, kwargs)['j'] >= 48

def entry_ema7_ema23_down_short(o, c, h, l, idx, **kwargs):
    if idx < 60 or c[idx] >= o[idx]: return False, {}
    s = _get_state(idx, o, c, h, l, kwargs)
    if (s['e7'] < s['e53'] and s['e23'] < s['e53']) and (l[idx] <= s['e7'] <= h[idx] and l[idx] <= s['e23'] <= h[idx]) and (c[idx] < s['e7']) and (50 <= s['k'] < 60 and s['j'] > 37 and s['k'] > 37) and (3 < s['k'] - s['d'] < 8) and (h[idx] < s['e53'] and h[idx-1] < s['e53_m1']): return True, {"price": c[idx]}
    return False, {}

def exit_ema7_ema23_down_short(c, h, l, idx, entry, **kwargs):
    s = _get_state(idx, [], c, h, l, kwargs)
    return s['k'] <= 35 and s['j'] <= 35

def entry_ema7_center_down_short(o, c, h, l, idx, **kwargs):
    if idx < 60 or c[idx] >= o[idx]: return False, {}
    params = kwargs.get("params") or {}
    s = _get_state(idx, o, c, h, l, kwargs)

    cci_arr_max = kwargs.get("cci_arr")
    prev_cci_max_lookback = int(params.get("lookback_prev_cci_max_lt_bars", 5))
    prev_cci_max_lt_level = params.get("prev_cci_max_less_than", 3.5)
    prev_cci_vals_for_max = []
    for kk in range(idx - prev_cci_max_lookback, idx):
        if kk < 0:
            return False, {}
        cur_cci_for_max = cci_arr_max[kk] if cci_arr_max is not None else _get_state(kk, o, c, h, l, kwargs)["cci"]
        prev_cci_vals_for_max.append(cur_cci_for_max)
    if not prev_cci_vals_for_max:
        return False, {}
    if not (max(prev_cci_vals_for_max) < prev_cci_max_lt_level):
        return False, {}


    j_arr_trigger = kwargs.get("j_arr")
    trigger_j_lookback = int(params.get("lookback_trigger_j_lt_bars", 5))
    trigger_j_lt_level = params.get("trigger_j_less_than", 106.5)
    for jj in range(idx - trigger_j_lookback, idx + 1):
        if jj < 0:
            return False, {}
        cur_j_trigger = j_arr_trigger[jj] if j_arr_trigger is not None else _get_state(jj, o, c, h, l, kwargs)["j"]
        if not (cur_j_trigger < trigger_j_lt_level):
            return False, {}


    signal_d_less_than = params.get("signal_d_less_than", 76)
    if not (s['d'] < signal_d_less_than): return False, {}

    cci_arr_prev = kwargs.get("cci_arr")
    prev_cci_lookback = int(params.get("lookback_prev_cci_gt_bars", 5))
    prev_cci_gt_threshold = params.get("prev_cci_greater_than", -50)
    prev_cci_ok = False
    for j in range(idx - prev_cci_lookback, idx):
        if j < 0:
            return False, {}
        cur_cci_prev = cci_arr_prev[j] if cci_arr_prev is not None else _get_state(j, o, c, h, l, kwargs)["cci"]
        if cur_cci_prev > prev_cci_gt_threshold:
            prev_cci_ok = True
            break
    if not prev_cci_ok: return False, {}



    k_arr, d_arr = kwargs.get("k_arr"), kwargs.get("d_arr")
    if idx <= 0:
        return False, {}
    prev_k_value = k_arr[idx - 1] if k_arr is not None else _get_state(idx-1, o, c, h, l, kwargs)["k"]
    signal_k_drop_min = params.get("signal_k_drop_min", 1.5)
    if not ((prev_k_value - s['k']) >= signal_k_drop_min): return False, {}

    if not (s['cci'] < params.get("signal_cci_less_than", -15)): return False, {}
    kd_diff_min = params.get("kd_diff_min", -3)
    kd_diff_max = params.get("kd_diff_max", 6)
    if not (kd_diff_min <= (s['k'] - s['d']) <= kd_diff_max): return False, {}
    if not (s['e6'] < s['e53']): return False, {}

    e95_arr = kwargs.get("e95_arr")
    cur_e95 = e95_arr[idx] if e95_arr is not None else ema(c[:idx+1], 95)
    if not (h[idx] < cur_e95): return False, {}

    prev_lookback = int(params.get("lookback_prev_k_ge_d_bars", 5))
    for i in range(idx - prev_lookback, idx):
        if i < 0:
            return False, {}
        if k_arr is not None and d_arr is not None:
            cur_k, cur_d = k_arr[i], d_arr[i]
        else:
            ps = _get_state(i, o, c, h, l, kwargs)
            cur_k, cur_d = ps["k"], ps["d"]
        if cur_k < cur_d:
            return False, {}

    cond_met = False
    e6_arr, e19_arr, e53_arr = kwargs.get("e6_arr"), kwargs.get("e19_arr"), kwargs.get("e53_arr")
    lookback = int(params.get("lookback_ema_order_bars", 5))
    for i in range(idx - lookback, idx + 1):
        if i < 0: continue
        cur_e6 = e6_arr[i] if e6_arr is not None else ema(c[:i+1], 6)
        cur_e19 = e19_arr[i] if e19_arr is not None else ema(c[:i+1], 19)
        cur_e53 = e53_arr[i] if e53_arr is not None else ema(c[:i+1], 53)
        if cur_e53 > cur_e6 > cur_e19:
            cond_met = True
            break
    if not cond_met: return False, {}

    green_lookback = int(params.get("lookback_green_bars", 5))
    required = int(params.get("required_green_close_count", 3))
    qualified_green = []
    for i in range(idx - green_lookback, idx):
        if i < 0:
            continue
        cur_e19 = e19_arr[i] if e19_arr is not None else ema(c[:i+1], 19)
        if c[i] > o[i] and c[i] > cur_e19 and c[idx] < c[i]:
            qualified_green.append(i)
    if len(qualified_green) < required: return False, {}

    return True, {"price": c[idx], "tp_class": 2, "entry_j": s['j'], "qualified_green": qualified_green}
def exit_ema7_center_down_short(c, h, l, idx, entry, **kwargs):
    params = kwargs.get("params") or {}
    s = _get_state(idx, c, c, h, l, kwargs)
    j_exit_level = params.get("class2_j_exit_less_than", 36.5)
    d_exit_level = params.get("exit_d_less_than", 62)
    cci_exit_level = params.get("exit_cci_less_than", -89)
    if s['j'] < j_exit_level and s['d'] < d_exit_level and s['cci'] < cci_exit_level:
        return True, {"price": c[idx], "exit_type": "\u6b62\u76c8\uff08j\u5c0f\u4e8e36.5\u4e14d\u5c0f\u4e8e62\u4e14cci\u5c0f\u4e8e\u8d1f89\uff09"}
    return False, {}
