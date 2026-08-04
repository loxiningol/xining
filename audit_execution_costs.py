# -*- coding: utf-8 -*-
"""Read-only OKX fee/fill friction audit; never places or amends orders."""
from __future__ import print_function

import json
import math

import auto_trade_okx as okx


def safe_float(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def main():
    fee = okx._okx_request("GET", "/api/v5/account/trade-fee",
                           params={"instType": "SWAP"}, auth=True)
    fills = okx._okx_request("GET", "/api/v5/trade/fills-history",
                             params={"instType": "SWAP", "limit": "100"}, auth=True)
    rows = []
    for fill in (fills.get("data") or []) if isinstance(fills, dict) else []:
        px = safe_float(fill.get("fillPx")); mark = safe_float(fill.get("fillMarkPx"))
        side = str(fill.get("side") or "").lower()
        adverse_bps = None
        if px and mark and mark > 0:
            adverse_bps = ((px/mark-1.0) if side == "buy" else
                           (mark/px-1.0))*10000.0
        rows.append({
            "instId": fill.get("instId"), "ordId": fill.get("ordId"),
            "execType": fill.get("execType"), "side": side,
            "fillPx": px, "fillMarkPx": mark,
            "adverse_vs_mark_bps": round(adverse_bps, 6) if adverse_bps is not None else None,
            "fee": safe_float(fill.get("fee")), "feeCcy": fill.get("feeCcy"),
            "fillSz": safe_float(fill.get("fillSz")),
            "fillTime": fill.get("fillTime"),
        })
    deviations = sorted(row["adverse_vs_mark_bps"] for row in rows
                        if row["adverse_vs_mark_bps"] is not None)
    def quantile(probability):
        if not deviations: return None
        position = (len(deviations)-1)*probability
        low = int(math.floor(position)); high = int(math.ceil(position))
        value = deviations[low] if low == high else (
            deviations[low] + (deviations[high]-deviations[low])*(position-low))
        return round(value, 6)
    fee_public = []
    for row in (fee.get("data") or []) if isinstance(fee, dict) else []:
        fee_public.append({key: row.get(key) for key in
                           ("level", "maker", "taker", "makerU", "takerU", "ruleType")})
    print(json.dumps({
        "fee_query_ok": str((fee or {}).get("code")) == "0",
        "fee_rates": fee_public,
        "fills_query_ok": str((fills or {}).get("code")) == "0",
        "fill_count": len(rows),
        "maker_count": sum(row["execType"] == "M" for row in rows),
        "taker_count": sum(row["execType"] == "T" for row in rows),
        "unique_order_count": len(set(row["ordId"] for row in rows if row["ordId"])),
        "adverse_vs_mark_bps": {"p50": quantile(.50), "p75": quantile(.75),
                                "p90": quantile(.90), "max": quantile(1.0)},
        "recent_fills": rows[:20],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
