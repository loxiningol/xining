#!/usr/bin/env python3
import sys
sys.path.insert(0, "/root")
import auto_trade_dual_engine_factory as d

fr = d._frame("SOL-USDT-SWAP", "15m")
print("SOL15 rows", len(fr), "cols", list(fr.columns)[:80])
fr2 = d._frame("DOGE-USDT-SWAP", "1h")
print("DOGE1h rows", len(fr2), "cols", list(fr2.columns)[:80])
fr3 = d._frame("BTC-USDT-SWAP", "15m")
print("BTC15 rows", len(fr3))
fr4 = d._frame("ETH-USDT-SWAP", "15m")
print("ETH15 rows", len(fr4) if fr4 is not None else None)
# feature presence
for name in ["volume", "funding", "swing", "prev_high", "atr", "upper_wick"]:
    hits = [c for c in fr.columns if name in str(c).lower()]
    print("feat", name, hits[:10])
