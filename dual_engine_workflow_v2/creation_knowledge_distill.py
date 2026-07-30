# -*- coding: utf-8 -*-
"""External knowledge distillation — microstructure truth cards for creation.

GLM/meta-think must read these cards before deepening a lens, so ideas are
checked against current-market invalidation notes (not static textbook lore).

Cards live under VECTOR_ROOT/auto_trade/dual_engine/knowledge_cards/.
A refresh script can later RAG-summarize papers/reports into the same schema.
"""
from __future__ import print_function

import json
import os
from datetime import datetime
from pathlib import Path


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _root():
    return Path(os.environ.get("VECTOR_ROOT") or "/root")


def cards_dir():
    return _root() / "auto_trade" / "dual_engine" / "knowledge_cards"


SEED_CARDS = (
    {
        "id": "card_funding_basis_crowding",
        "title_zh": "资金费率拥挤与均值回归衰减",
        "domain": "crypto_perp_microstructure",
        "truth_zh": "极端资金费率拥挤后的反向，在高换手时段常被抢跑；裸费率信号扣除成本后边缘变薄。",
        "invalidates_lenses": ["inventory", "mean_reversion", "pairs"],
        "supports_lenses": ["vol_squeeze", "trend", "momentum"],
        "freshness_days": 30,
        "source": "seed_distill_v1",
    },
    {
        "id": "card_liq_sweep_toxicity",
        "title_zh": "流动性扫荡毒性上升",
        "domain": "crypto_perp_microstructure",
        "truth_zh": "假突破/扫止损后的回收在 2024–2026 永续上更常变成趋势加速，单纯 fade 扫荡胜率下降。",
        "invalidates_lenses": ["liquidity", "sweep", "sfp"],
        "supports_lenses": ["breakout", "trend", "mom_vol"],
        "freshness_days": 30,
        "source": "seed_distill_v1",
    },
    {
        "id": "card_vol_compression_still_works",
        "title_zh": "波动压缩后的方向选择仍有结构边缘",
        "domain": "crypto_perp_microstructure",
        "truth_zh": "低波收口后的扩张突破在多币种 5m/15m 上仍反复出现，但需高波分位禁入，否则假突破吃掉收益。",
        "invalidates_lenses": [],
        "supports_lenses": ["vol_squeeze", "breakout", "mom_vol", "dual_ma"],
        "freshness_days": 30,
        "source": "seed_distill_v1",
    },
    {
        "id": "card_pairs_crypto_beta",
        "title_zh": "加密配对受共同 beta 主导",
        "domain": "crypto_perp_microstructure",
        "truth_zh": "BTC beta 主导下，山寨配对协整在危机日同步断裂；短窗高相关不等于可交易价差边缘。",
        "invalidates_lenses": ["pairs", "cointegration"],
        "supports_lenses": ["trend", "momentum"],
        "freshness_days": 45,
        "source": "seed_distill_v1",
    },
)


def ensure_seed_cards():
    d = cards_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / "microstructure_truth_cards.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("cards"):
                return path, data
        except Exception:
            pass
    payload = {
        "schema": "qiyu_microstructure_truth_cards_v1",
        "updated_at": _now(),
        "note_zh": "种子卡片；可用 distill_refresh 覆盖为 RAG 摘要。",
        "cards": list(SEED_CARDS),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, payload


def load_cards():
    path, data = ensure_seed_cards()
    return {
        "ok": True,
        "path": str(path),
        "n": len(data.get("cards") or []),
        "cards": data.get("cards") or [],
        "updated_at": data.get("updated_at"),
        "at": _now(),
    }


def lens_tokens(design_doc):
    div = (design_doc or {}).get("divergence") or {}
    parts = [
        (design_doc or {}).get("mechanism_family") or "",
        (design_doc or {}).get("core_logic_zh") or "",
        div.get("selected_id") or "",
        div.get("selected_lens_zh") or "",
    ]
    text = " ".join(parts).lower()
    return text


def assess_lens_against_cards(design_doc, cards=None):
    """Force meta-think to confront whether the chosen lens is currently stale."""
    pack = load_cards() if cards is None else {"cards": cards, "ok": True}
    cards = pack.get("cards") or []
    text = lens_tokens(design_doc)
    hits_invalid = []
    hits_support = []
    for c in cards:
        inv = [str(x).lower() for x in (c.get("invalidates_lenses") or [])]
        sup = [str(x).lower() for x in (c.get("supports_lenses") or [])]
        if any(tok and tok in text for tok in inv):
            hits_invalid.append(c)
        if any(tok and tok in text for tok in sup):
            hits_support.append(c)

    # Fail hard only when invalidated and not also supported by a fresher card
    passed = True
    reason = None
    if hits_invalid and not hits_support:
        passed = False
        reason = "lens_invalidated_by_truth_cards"
    elif hits_invalid and hits_support:
        # contested — require explicit invalidation note on design
        if not (design_doc or {}).get("invalidation_zh"):
            passed = False
            reason = "contested_lens_needs_invalidation_zh"

    return {
        "ok": True,
        "schema": "qiyu_knowledge_distill_gate_v1",
        "passed": passed,
        "reason": reason,
        "n_cards": len(cards),
        "invalidating_cards": [
            {"id": c.get("id"), "title_zh": c.get("title_zh"), "truth_zh": c.get("truth_zh")}
            for c in hits_invalid
        ],
        "supporting_cards": [
            {"id": c.get("id"), "title_zh": c.get("title_zh")}
            for c in hits_support
        ],
        "cards_brief": [
            {"id": c.get("id"), "title_zh": c.get("title_zh"), "truth_zh": c.get("truth_zh")}
            for c in cards[:8]
        ],
        "at": _now(),
        "human_banner_zh": (
            None if passed else
            "【外部认知卡片否决】所选微观结构视角在当前市场语境下可能已失效："
            + ",".join(c.get("id") for c in hits_invalid)
            + "。换视角或补失效条件。"
        ),
        "note_zh": "创造前必须阅读微观真相卡片，禁止刻舟求剑。",
    }


def distill_refresh_stub(extra_cards=None):
    """Merge extra cards (from RAG/ cron) into the store."""
    path, data = ensure_seed_cards()
    cards = list(data.get("cards") or [])
    by_id = {c.get("id"): c for c in cards}
    for c in extra_cards or []:
        if c.get("id"):
            by_id[c["id"]] = c
    data["cards"] = list(by_id.values())
    data["updated_at"] = _now()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(path), "n": len(data["cards"]), "at": _now()}
