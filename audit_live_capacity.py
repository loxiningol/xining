import json
import sys

sys.path.insert(0, "/root")
import auto_trade_okx as okx

SYMBOLS = [
    "BTC-USDT-SWAP",
    "CL-USDT-SWAP",
    "XAU-USDT-SWAP",
    "NG-USDT-SWAP",
]


def request(path, params=None, auth=False):
    raw = okx._okx_request("GET", path, params=params or {}, auth=auth)
    if str(raw.get("code")) != "0":
        raise RuntimeError({"path": path, "code": raw.get("code"), "msg": raw.get("msg")})
    return raw.get("data") or []


balance_rows = request("/api/v5/account/balance", auth=True)
usdt = {}
nonzero_balances = []
if balance_rows:
    for detail in balance_rows[0].get("details") or []:
        try:
            nonzero = any(
                abs(float(detail.get(key) or 0)) > 1e-12
                for key in ["eq", "cashBal", "availBal", "availEq", "frozenBal", "upl"]
            )
        except (TypeError, ValueError):
            nonzero = False
        if nonzero:
            nonzero_balances.append({
                key: detail.get(key)
                for key in [
                    "ccy", "eq", "cashBal", "availBal", "availEq",
                    "frozenBal", "upl", "eqUsd",
                ]
            })
        if detail.get("ccy") == "USDT":
            usdt = {
                key: detail.get(key)
                for key in [
                    "ccy", "eq", "cashBal", "availBal", "availEq",
                    "frozenBal", "upl", "mgnRatio",
                ]
            }
            break

positions = []
for row in request("/api/v5/account/positions", auth=True):
    try:
        if float(row.get("pos") or 0) == 0:
            continue
    except (TypeError, ValueError):
        continue
    positions.append({
        key: row.get(key)
        for key in ["instId", "pos", "posSide", "avgPx", "lever", "mgnMode", "upl"]
    })

pending = []
for row in request(
    "/api/v5/trade/orders-pending", {"instType": "SWAP"}, auth=True
):
    pending.append({
        key: row.get(key)
        for key in ["instId", "ordId", "side", "posSide", "sz", "state"]
    })

instruments = {}
for symbol in SYMBOLS:
    rows = request(
        "/api/v5/public/instruments",
        {"instType": "SWAP", "instId": symbol},
        auth=False,
    )
    row = rows[0] if rows else {}
    instruments[symbol] = {
        key: row.get(key)
        for key in [
            "instId", "state", "ctVal", "ctValCcy", "ctType", "settleCcy",
            "lotSz", "minSz", "tickSz", "maxLmtSz", "maxMktSz",
        ]
    }

leverage = {}
for symbol in SYMBOLS:
    try:
        leverage[symbol] = request(
            "/api/v5/account/leverage-info",
            {"instId": symbol, "mgnMode": "cross"},
            auth=True,
        )
    except Exception as exc:
        leverage[symbol] = {"error": str(exc)}

account_config = request("/api/v5/account/config", auth=True)
try:
    fee_rates = request(
        "/api/v5/account/trade-fee", {"instType": "SWAP"}, auth=True
    )
except Exception as exc:
    fee_rates = {"error": str(exc)}

try:
    funding_rows = request("/api/v5/asset/balances", auth=True)
    funding_balances = []
    for row in funding_rows:
        try:
            if abs(float(row.get("bal") or 0)) <= 1e-12:
                continue
        except (TypeError, ValueError):
            continue
        funding_balances.append({
            key: row.get(key)
            for key in ["ccy", "bal", "availBal", "frozenBal"]
        })
except Exception as exc:
    funding_balances = {"error": str(exc)}

print(json.dumps({
    "ok": True,
    "usdt": usdt,
    "nonzero_balances": nonzero_balances,
    "account_total": ({
        key: balance_rows[0].get(key)
        for key in ["totalEq", "isoEq", "adjEq", "availEq"]
    } if balance_rows else {}),
    "positions": positions,
    "pending_orders": pending,
    "instruments": instruments,
    "leverage": leverage,
    "account_config": account_config,
    "fee_rates": fee_rates,
    "funding_balances": funding_balances,
}, ensure_ascii=False))
