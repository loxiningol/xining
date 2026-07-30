# -*- coding: utf-8 -*-
"""Creation-stage professional research (pre-review).

Pipeline (order → submit review):
  1) EasyQuant-style factor mining on OKX candles
  2) QuantOracle certify top factor return series (Sharpe/Kelly/Hurst/…)
  3) Build GLM-facing professional brief (no invented math)

This stage never mounts and never skips ADA5 admission later.
"""
from __future__ import print_function

from datetime import datetime

from . import easyquant_bridge as eq
from . import quantoracle_bridge as qo


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_creation_research(symbol, timeframe, direction="long", brief="",
                          horizon=3, top_k=4):
    probe_eq = eq.probe_easyquant()
    probe_qo = qo.probe()
    mine = eq.mine_factors(
        symbol=symbol,
        timeframe=timeframe,
        horizon=horizon,
        top_k=top_k,
    )
    certified = []
    for fac in (mine.get("factors") or []):
        st = fac.get("stats") or {}
        cert = qo.certify_factor_signal(
            returns=fac.get("returns") or [],
            win_rate=st.get("win_rate"),
            avg_win=st.get("avg_win"),
            avg_loss=st.get("avg_loss"),
            equity_curve=fac.get("equity_curve"),
        )
        # drop heavy series from packed output
        certified.append({
            "factor": fac.get("factor"),
            "rule": fac.get("rule"),
            "thesis_zh": fac.get("thesis_zh"),
            "score": fac.get("score"),
            "stats": st,
            "quantoracle": {
                "ok": cert.get("ok"),
                "source": cert.get("source"),
                "certified": cert.get("certified"),
                "error": cert.get("error"),
                "note_zh": cert.get("note_zh"),
            },
        })

    envelope = eq.modeling_envelope(
        brief=brief,
        focus={"symbol": symbol, "timeframe": timeframe, "direction": direction},
        probe=probe_eq,
        mine=mine,
    )
    # Prefer factors with QuantOracle-certified positive edge
    preferred = []
    for row in certified:
        c = (row.get("quantoracle") or {}).get("certified") or {}
        sharpe = c.get("sharpe_ratio")
        mean_net = (row.get("stats") or {}).get("mean_net")
        if mean_net is not None and mean_net > 0 and (sharpe is None or float(sharpe) > 0):
            preferred.append(row)

    glm_research_brief = {
        "schema": "qiyu_creation_research_brief_v1",
        "human_brief": brief,
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "quantoracle_probe_ok": bool(probe_qo.get("ok")),
        "easyquant_probe_ok": bool(probe_eq.get("ok")),
        "preferred_factors": preferred[:3],
        "all_certified_top": certified,
        "instructions_zh": (
            "你是总设计师。以下因子统计由 EasyQuant 风格挖掘器产出，"
            "风险指标由 QuantOracle 确定性计算认证（或本地可复现兜底，见 source 字段）。"
            "请据此撰写 mechanism_spec：必须点名采用的因子/规则与对手方，"
            "禁止编造与下列 certified 数字冲突的胜率/夏普。"
            "不可协商：protective SL 0.9%；创造完成后仍须过 ADA5 四复核。"
        ),
        "built_at": _now(),
    }

    return {
        "ok": bool(mine.get("ok")) and bool(certified),
        "schema": "qiyu_creation_research_stage_v1",
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "easyquant_probe": probe_eq,
        "quantoracle_probe": probe_qo,
        "factor_mine": {
            "ok": mine.get("ok"),
            "n_bars": mine.get("n_bars"),
            "candle_path": mine.get("candle_path"),
            "all_scores": mine.get("all_scores"),
            "note_zh": mine.get("note_zh"),
        },
        "certified_factors": certified,
        "easyquant_envelope": envelope,
        "glm_research_brief": glm_research_brief,
        "at": _now(),
    }
