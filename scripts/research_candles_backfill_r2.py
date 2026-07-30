#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill OKX candle history into S3/R2 (or local mirror).

Does NOT modify formal_* live short caches.
Does NOT place trades.

Usage:
  # local mirror (no cloud keys) — smoke / transition
  QIYU_RESEARCH_CANDLES_MODE=local \\
    python3 scripts/research_candles_backfill_r2.py \\
      --symbol BTC-USDT-SWAP --bar 5m --since 2024-01-01

  # Cloudflare R2
  # set QIYU_R2_* in ai_ecosystem.env then:
    python3 scripts/research_candles_backfill_r2.py \\
      --symbol ADA-USDT-SWAP --bar 5m --since 2024-01-01
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(os.environ.get("VECTOR_ROOT") or "/root")
sys.path.insert(0, str(ROOT))
# also allow repo-local dual_engine when run from workspace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(str(ROOT))


def _load_env_file(path):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _parse_since(s):
    s = str(s).strip()
    if s.isdigit():
        t = int(s)
        return t if t > 1e12 else t * 1000
    dt = datetime.strptime(s[:10], "%Y-%m-%d")
    return int(dt.timestamp() * 1000)


def _okx_history_page(inst_id, bar, after=None, limit=100):
    """Fetch one page via auto_trade_okx if present, else public urllib."""
    params = {"instId": inst_id, "bar": bar, "limit": str(limit)}
    if after is not None:
        params["after"] = str(after)
    try:
        import auto_trade_okx as okx
        raw = okx._okx_request("GET", "/api/v5/market/history-candles", params=params, auth=False)
        return raw
    except Exception as primary:
        # fallback public
        import urllib.parse
        import urllib.request
        q = urllib.parse.urlencode(params)
        url = "https://www.okx.com/api/v5/market/history-candles?" + q
        req = urllib.request.Request(
            url, headers={"User-Agent": "qiyu-research-backfill/1.0", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            return {"code": "-1", "msg": "okx_fail:%s|%s" % (primary, exc), "data": []}


def _closed_rows(raw):
    rows = raw.get("data") if isinstance(raw, dict) and str(raw.get("code")) == "0" else []
    out = []
    for row in rows or []:
        if not isinstance(row, list) or len(row) < 5:
            continue
        # confirm flag at [8] when present
        if len(row) >= 9 and str(row[8]) not in ("1", "true", "True"):
            continue
        try:
            out.append({
                "ts": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["ts"])
    return out


def fetch_history(inst_id, bar, since_ms, until_ms=None, sleep_sec=0.25, max_pages=5000):
    until_ms = int(until_ms or int(time.time() * 1000))
    after = None
    by_ts = {}
    pages = 0
    while pages < int(max_pages):
        raw = _okx_history_page(inst_id, bar, after=after, limit=100)
        page = _closed_rows(raw)
        pages += 1
        if not page:
            print("PAGE_EMPTY", pages, (raw or {}).get("msg") or (raw or {}).get("code"), flush=True)
            break
        for r in page:
            if r["ts"] < since_ms:
                continue
            if r["ts"] > until_ms:
                continue
            by_ts[r["ts"]] = r
        oldest = min(r["ts"] for r in page)
        print(
            "PAGE", pages, "got", len(page), "oldest", oldest,
            "unique", len(by_ts), flush=True,
        )
        if oldest <= since_ms:
            break
        if after is not None and oldest >= after:
            break
        after = oldest
        time.sleep(float(sleep_sec))
    rows = [by_ts[k] for k in sorted(by_ts)]
    return rows


def main():
    ap = argparse.ArgumentParser(description="Backfill OKX candles to R2/S3/local research store")
    ap.add_argument("--symbol", required=True, help="e.g. ADA-USDT-SWAP")
    ap.add_argument("--bar", default="5m", help="5m|15m|1H")
    ap.add_argument("--since", default="2024-01-01")
    ap.add_argument("--until", default="", help="YYYY-MM-DD or ms; default now")
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--env-file", default="/root/auto_trade/ai_ecosystem.env")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    _load_env_file(args.env_file)
    # also try workspace example path
    _load_env_file(str(Path(__file__).resolve().parents[1] / "ai_ecosystem.env"))

    from dual_engine_workflow_v2 import research_candle_store as rcs

    probe = rcs.probe()
    print("PROBE", json.dumps(probe, ensure_ascii=False), flush=True)
    if not probe.get("ok"):
        print("FATAL store not ready:", probe.get("error") or probe.get("hint_zh"))
        return 2

    store, backend = rcs.get_store()
    since_ms = _parse_since(args.since)
    until_ms = _parse_since(args.until) if args.until else int(time.time() * 1000)
    bar = rcs._norm_bar(args.bar)
    print("FETCH", args.symbol, bar, "since", since_ms, "until", until_ms, "backend", backend, flush=True)

    rows = fetch_history(args.symbol, bar, since_ms, until_ms=until_ms, sleep_sec=args.sleep)
    print("FETCHED", len(rows), flush=True)
    if not rows:
        return 3

    by_month = defaultdict(list)
    for r in rows:
        by_month[rcs.yyyymm_of_ts(r["ts"])].append(r)

    chunks = []
    for ym in sorted(by_month.keys()):
        candles = by_month[ym]
        candles.sort(key=lambda x: x["ts"])
        meta = {
            "yyyymm": ym,
            "key": rcs.chunk_key(args.symbol, bar, ym),
            "start_ts": candles[0]["ts"],
            "end_ts": candles[-1]["ts"],
            "n": len(candles),
        }
        print("CHUNK", ym, "n", len(candles), flush=True)
        if args.dry_run:
            chunks.append(meta)
            continue
        put = rcs.put_chunk(args.symbol, bar, ym, candles, store=store)
        print("PUT", put.get("ok"), put.get("error") or put.get("key"), flush=True)
        if not put.get("ok"):
            return 4
        chunks.append(meta)

    manifest = {
        "schema": rcs.SCHEMA_MANIFEST,
        "instId": args.symbol.upper(),
        "bar": bar,
        "since_ms": since_ms,
        "until_ms": until_ms,
        "n_total": len(rows),
        "chunks": chunks,
        "source": "okx_history_candles",
    }
    if not args.dry_run:
        saved = rcs.save_manifest(args.symbol, bar, manifest, store=store)
        print("MANIFEST", saved, flush=True)
        if not saved.get("ok"):
            return 5
    else:
        print("DRY_RUN_MANIFEST", json.dumps(manifest, ensure_ascii=False)[:800], flush=True)

    print("DONE", args.symbol, bar, "chunks", len(chunks), "n", len(rows), "backend", backend)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        print("FATAL", exc)
        raise
