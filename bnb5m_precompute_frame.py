#!/usr/bin/env python3
"""Build a compact BNB 5m frame with session/vol proxies for frost3_bnb5m_session."""
from __future__ import print_function
import os
import pickle
import sys

OUT = "/root/auto_trade/dual_engine/frost3_bnb5m_session_frame.pkl"
SRC = "/root/auto_trade/codex_0725_train5/frame_BNB_USDT_SWAP_5m.pkl"
MAX_BARS = int(os.environ.get("BNB5M_MAX_BARS", "3500"))
SESSION_UTC_START = 7
SESSION_UTC_END = 20


def main():
    print("loading", SRC, flush=True)
    fr = pickle.load(open(SRC, "rb"))
    if len(fr) > MAX_BARS:
        fr = fr.iloc[-MAX_BARS:].copy()
    else:
        fr = fr.copy()
    hour = fr.index.hour.astype(float)
    fr["hour_utc"] = hour
    fr["session_liq"] = ((hour >= SESSION_UTC_START) & (hour < SESSION_UTC_END)).astype(float)
    base = fr["atr14"].rolling(48, min_periods=12).mean()
    fr["vol_pulse_proxy"] = (fr["atr14"] / base.replace(0, float("nan"))).fillna(1.0)
    pickle.dump(fr, open(OUT, "wb"), protocol=2)
    print("wrote", OUT, "rows", len(fr), "cols", len(fr.columns), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
