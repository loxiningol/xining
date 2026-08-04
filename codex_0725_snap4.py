# -*- coding: utf-8 -*-
from __future__ import print_function
import copy, json, os
from pathlib import Path

for line in Path("/root/auto_trade/ai_ecosystem.env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

import auto_trade_human_confirm_pipeline as pipeline
import auto_trade_strategy_dsl as dsl
import auto_trade_strategy_ecosystem as eco

store = json.loads(Path("/root/strategy_configs/ai_dsl_strategies.json").read_text())
base = next(s for s in store["strategies"] if s.get("key") == "ada5_z20_t60_prev_h14_0724k")

# include ADA for sanity + many alts
syms = [
    "ADA-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "DOGE-USDT-SWAP",
    "XRP-USDT-SWAP", "LINK-USDT-SWAP", "OP-USDT-SWAP", "BTC-USDT-SWAP",
    "BNB-USDT-SWAP", "LTC-USDT-SWAP", "SUI-USDT-SWAP", "AVAX-USDT-SWAP",
    "APT-USDT-SWAP", "DOT-USDT-SWAP", "NEAR-USDT-SWAP", "INJ-USDT-SWAP",
    "TRX-USDT-SWAP", "TON-USDT-SWAP", "FIL-USDT-SWAP", "ATOM-USDT-SWAP",
    "ARB-USDT-SWAP", "SEI-USDT-SWAP", "TIA-USDT-SWAP", "ORDI-USDT-SWAP",
]


def mets(trades, result):
    pnls = [float(t.get("pnl_ratio") or 0) for t in trades]
    n = len(pnls)
    mean = (sum(pnls) / n) if n else 0
    wr = (sum(1 for p in pnls if p > 0) / n * 100) if n else 0
    st = mx = 0
    for p in pnls:
        if p <= 0:
            st += 1
            mx = max(mx, st)
        else:
            st = 0
    fold = False
    try:
        fold, _, _ = pipeline._five_fold_pass(trades, min_positive=4)
    except Exception:
        pass
    return n, round(wr, 1), round(mean, 4), mx, fold, round(float(result.get("total_return_percent") or 0), 1)


rows = []
params = [
    (42, 2.3, 14), (42, 2.5, 14), (42, 2.0, 14),
    (45, 2.3, 14), (40, 2.3, 16), (38, 2.5, 18),
]
for sym in syms:
    try:
        f = pipeline._frame(sym, "5m")
        if len(f) > 12000:
            f = f.iloc[-12000:]
        fr = eco._friction_scenario(sym, "observed_base")
        print("FRAME", sym, len(f), flush=True)
    except Exception as e:
        print("NOFRAME", sym, e, flush=True)
        continue
    for rc, z, hold in params:
        obj = copy.deepcopy(base)
        obj["key"] = "snap_%s_r%s_z%s_h%s" % (
            sym.split("-")[0].lower(), rc, str(z).replace(".", "p"), hold)
        obj["supported_instruments"] = [sym]
        obj["max_hold_bars"] = hold
        for k in ("live_enabled", "auto_trade_eligible", "approved_version_hash"):
            obj.pop(k, None)
        for c in obj["entry"]["all"]:
            if c.get("id") == "rsi":
                c["right"] = {"value": float(rc)}
            if c.get("id") == "z":
                c["right"] = {"value": float(z)}
        try:
            obj = dsl.validate_strategy(obj)
            res = dsl.backtest_dsl(
                f, obj, leverage=20, stop_loss_pct=0.009,
                fee_rate_per_side=float(fr.get("fee_rate_per_side") or 0.0005),
                slippage_rate_per_side=float(fr.get("slippage_rate_per_side") or 0.0002),
                half_spread_rate_per_side=float(fr.get("half_spread_rate_per_side") or 0),
                impact_rate_per_side=float(fr.get("impact_rate_per_side") or 0),
                latency_rate_per_side=float(fr.get("latency_rate_per_side") or 0),
                funding_rate_per_8h=float(fr.get("funding_rate_per_8h") or 0),
                friction_scenario="observed_base",
            )
            n, wr, mean, st, fold, ret = mets(res.get("trades") or [], res)
        except Exception as e:
            print("FAIL", sym, e, flush=True)
            continue
        rows.append((wr, n, mean, st, fold, ret, sym, rc, z, hold))
        print("%s r=%s z=%s h=%s -> n=%s wr=%s mean=%s st=%s fold=%s ret=%s" % (
            sym, rc, z, hold, n, wr, mean, st, fold, ret), flush=True)

rows.sort(reverse=True)
print("=== BEST15 ===", flush=True)
for r in rows[:15]:
    print(r, flush=True)
print("=== KEEPABLE ===", flush=True)
keep = []
for r in rows:
    wr, n, mean, st, fold, ret, sym, rc, z, hold = r
    if (sym != "ADA-USDT-SWAP" and n >= 16 and wr >= 55 and mean > 0 and st <= 4
            and (fold or (n >= 22 and wr >= 57))):
        keep.append(r)
        print("KEEP", r, flush=True)
print("KEEP_N", len(keep), flush=True)
Path("/root/auto_trade/codex_0725_train4/snap4.json").write_text(
    json.dumps([{"wr": a, "n": b, "mean": c, "st": d, "fold": e, "ret": f,
                 "sym": g, "rc": h, "z": i, "hold": j}
                for a, b, c, d, e, f, g, h, i, j in rows[:40]],
               ensure_ascii=False, indent=2), encoding="utf-8")
print("DONE", flush=True)
