# 薄中枢多周期 / 多族重构蓝图 v2

权威：用户终局裁决 + 三项强制增补。产出效率优先；禁止快速结案躺平；禁止为凑 n 放宽参数。

---

## 合理性确认（增补）

| 增补 | 结论 |
|---|---|
| 1. `MIN_EVALS_PER_COMBO=8` + ≥3 种 timing 指纹 | **采纳**。原 `N_FAIL=2` 过快耗尽池，会合法躺平。 |
| 2. 原子组多样性 | **采纳意图**（跨组探索）。修正：现网 1h 上 `cross_*` **不是**高频；实现记「跨组才计深度」，**不**把 cross 标成频率优先。 |
| 3. 换周期重置 held/xwin/atr/hold | **采纳**。不同 TF 几何不可继承。 |

---

## §1.3 跨周期 + 最小探索深度 + 几何重置（代码级）

```python
# thin_create_policy / loop state
TIMEFRAME_POOL = ["15m", "1h", "4h"]
MIN_EVALS_PER_COMBO = 8
MIN_DISTINCT_TIMING_FPS = 3
# 删除 N_FAIL_LIMIT_PER_TF

def timing_fingerprint(timing):
    # 规范序：factor|op|value|window
    rows = []
    for t in timing or []:
        rows.append("%s|%s|%s|%s" % (
            t.get("factor"), t.get("operator"), t.get("value"), t.get("window")))
    return tuple(sorted(rows))

def combo_key(tf, family):
    return "%s|%s" % (tf, family)

def on_eval_done(state, recipe, result):
    tf = recipe["timeframe"]
    fam = recipe["family"]
    key = combo_key(tf, fam)
    slot = state["combos"].setdefault(key, {
        "evals": 0, "timing_fps": set(), "depth_ok": False,
    })
    fp = timing_fingerprint(recipe.get("timing"))
    group_ok = state.get("last_group_diverse", True)  # §4.3：同组连刷不计
    if group_ok:
        slot["evals"] += 1
        slot["timing_fps"].add(fp)
    else:
        # 不计探索深度；批评器已警告
        pass
    slot["depth_ok"] = (
        slot["evals"] >= MIN_EVALS_PER_COMBO
        and len(slot["timing_fps"]) >= MIN_DISTINCT_TIMING_FPS
    )

    n = int(result.get("n") or 0)
    s1 = (result.get("stage") == "S1_n") or (n < 30)

    if s1 and not slot["depth_ok"]:
        # 禁止切 TF / 换族 / 结案；逼换不同合法 timing 叶
        return {"action": "refine_timing_diverse", "combo": key, "slot": slot}

    if s1 and slot["depth_ok"]:
        # 跨周期验证：新研究合同
        nxt = next_tf(state, tf)
        if nxt:
            return switch_tf_contract(state, recipe, nxt)
        return try_switch_family_or_close(state, recipe)

def switch_tf_contract(state, recipe, new_tf):
    # 强制重置时间相关几何 —— 禁止继承
    reset_keys = ("held", "xwin", "atr", "hold")  # z 可选用默认
    new_recipe = {
        "family": recipe["family"],
        "symbol": recipe["symbol"],
        "timeframe": new_tf,
        # held/xwin/atr/hold 由 LLM 按新周期重填；锁里清空
        "timing": list(DEFAULT_TIMING),  # skdj_diff/-20 + macd_hist/-1
    }
    state["lock_recipe"] = None
    state["stalled"] = 0
    state["micro_done"] = False
    state["adjusts"] = 0
    state["tf_tried"].append(new_tf)
    emit({"phase": "cross_tf_contract", "from": recipe["timeframe"], "to": new_tf,
          "note_zh": "跨周期验证；几何已重置"})
    prompt = (
        "新周期为%s，请根据该周期波动与K线密度重新计算 held/xwin/atr/hold"
        "（例如15m持有根数应小于1h），禁止直接复制上一周期的整数。"
        "输出完整 recipe（含 timeframe=%s）。" % (new_tf, new_tf)
    )
    return {"action": "new_contract", "recipe_seed": new_recipe, "prompt": prompt}
```

---

## §2.1 身份锁 / 探索预算（代码级）

```python
LOCK_KEYS_HARD = ("held", "xwin", "fast", "slow", "z", "atr", "hold")
# family/symbol 受预算约束，非一刀切死锁

EXPLORE_BUDGET = {
    "family_order": ["xu_long", "xd_short", "pb_long", "pb_short"],
}

def may_switch_family(state, recipe):
    key = combo_key(recipe["timeframe"], recipe["family"])
    slot = state["combos"].get(key) or {}
    # 必须先耗尽该（tf+family）最小探索深度
    if not slot.get("depth_ok"):
        return False
    return True

def switch_family(state, recipe):
    assert may_switch_family(state, recipe)
    nxt = next_family(state, recipe["family"])
    if not nxt:
        return {"action": "close", "reason": "evidence_exhausted"}
    state["stalled"] = 0
    state["micro_done"] = False
    state["adjusts"] = 0
    state["lock_recipe"] = None  # 换族后几何+timing 重种子
    emit({"phase": "family_switch", "from": recipe["family"], "to": nxt})
    return {"action": "switch_family", "family": nxt, "timing": list(DEFAULT_TIMING)}
```

---

## §4.3 多样性（代码级）

```python
# thin_timing_atoms.py
ATOM_OP_GROUP = {
    "cross_up": "cross",
    "cross_down": "cross",
    "above": "level",
    "below": "level",
    "between": "level",
}
# 说明：cross 表示「穿越类」组标签，不等于「高频」；1h 上 cross 常更稀。

def timing_groups(timing):
    gs = set()
    for t in timing or []:
        gs.add(ATOM_OP_GROUP.get(str(t.get("operator")), "other"))
    return gs

def diversity_gate(history_timings, new_timing):
    """连续 3 次 Refine 若组集合无变化且均为单组 → 不计 MIN_EVALS 深度。"""
    recent = (history_timings + [new_timing])[-3:]
    if len(recent) < 3:
        return True, ""
    groups = [timing_groups(t) for t in recent]
    # 全是单组且组相同（例如三次都只有 level）
    if all(len(g) == 1 for g in groups) and len(set(frozenset(g) for g in groups)) == 1:
        return False, (
            "原子类型重复，请切换至另一操作组（cross ↔ level）重新尝试，"
            "否则不计入8次探索深度。"
        )
    return True, ""
```

`timing_stage.build_critique`：若 `diversity_gate` 失败，追加上述警告到 critique；`actions_allowed` 不含 `loosen_timing`。

---

## 默认种子（PR-A 锁定）

```python
DEFAULT_TIMING = [
    {"factor": "skdj_diff", "operator": "above", "value": -20, "window": 9},
    {"factor": "macd_hist", "operator": "above", "value": -1},
]
```

禁止默认 `skdj_kd cross_up`。

---

## PR 顺序

- **PR-A**：`thin_timing_atoms.py` + 创造/loop 读白名单 + 默认种子锁定上式  
- PR-B：探索深度计数 + S1 文案 + §4.3 组门  
- PR-C：TF 池 + cross_tf_contract + 几何重置  
- PR-D：换族预算  

共享文件经 `cursor/shared-*` PR → CI → 合并 → 部署。
