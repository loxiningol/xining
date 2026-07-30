# -*- coding: utf-8 -*-
"""Memory guard for auto_driver / STEP A matrix — protect Web on low-RAM hosts.

Design for ~764MB Vultr hosts:
  - Soft ceiling ≈ 45% of total RAM for the auto_driver process RSS
  - Hard refuse matrix expansion when MemAvailable is critically low
  - Never claim 1.5GB budgets on sub-1GB machines
"""
from __future__ import print_function

import gc
import os
import time


def read_meminfo_mb():
    total = avail = None
    try:
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) // 1024
    except Exception:
        pass
    if total is None:
        total, avail = 1024, 512
    if avail is None:
        avail = max(0, int(total) // 4)
    return int(total), int(avail)


def process_rss_mb():
    try:
        with open("/proc/self/status", "r") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    try:
        import resource
        # ru_maxrss is KB on Linux
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) // 1024
    except Exception:
        return 0


def soft_rss_limit_mb(total_mb=None):
    """Process soft RSS budget — leave headroom for qiyu-web + OS."""
    if total_mb is None:
        total_mb, _ = read_meminfo_mb()
    total_mb = int(total_mb or 764)
    if total_mb <= 1024:
        # 764MB host → ~340MB soft cap for driver
        return max(220, int(total_mb * 0.45))
    if total_mb <= 4096:
        return int(total_mb * 0.50)
    return min(1536, int(total_mb * 0.40))


def install_soft_rlimit(total_mb=None):
    """Best-effort RLIMIT_AS / RLIMIT_DATA. Fail-open if unsupported."""
    limit_mb = soft_rss_limit_mb(total_mb)
    # Address-space limit is coarser than RSS; use ~2.2× soft RSS as AS ceiling
    as_bytes = int(limit_mb * 1024 * 1024 * 2.2)
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        new_hard = hard if hard > 0 else as_bytes
        new_soft = min(as_bytes, new_hard) if new_hard > 0 else as_bytes
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, new_hard if new_hard > 0 else new_soft))
        return {"ok": True, "soft_rss_mb": limit_mb, "as_bytes": new_soft}
    except Exception as exc:
        return {"ok": False, "soft_rss_mb": limit_mb, "error": str(exc)}


def force_release(label=""):
    gc.collect()
    try:
        import auto_trade_human_confirm_pipeline as pipe
        # Trim cache if available
        if hasattr(pipe, "trim_frame_cache"):
            pipe.trim_frame_cache(max_entries=4)
        elif hasattr(pipe, "_FRAME_CACHE"):
            # Keep at most 3 hottest keys
            cache = pipe._FRAME_CACHE
            if isinstance(cache, dict) and len(cache) > 4:
                keys = list(cache.keys())
                for k in keys[:-3]:
                    cache.pop(k, None)
    except Exception:
        pass
    gc.collect()
    total, avail = read_meminfo_mb()
    print(
        "[memory_guard] release%s rss=%dMB avail=%dMB/%dMB"
        % ((":%s" % label if label else ""), process_rss_mb(), avail, total),
        flush=True,
    )
    return avail


def matrix_budget(total_mb=None, avail_mb=None):
    """Decide how wide the matrix may be given live RAM.

    Returns dict:
      mode: "primary" | "anchor" | "full"
      max_symbols: int
      reason: str
    """
    if total_mb is None or avail_mb is None:
        total_mb, avail_mb = read_meminfo_mb()
    rss = process_rss_mb()
    soft = soft_rss_limit_mb(total_mb)
    # Critical: protect web — never expand matrix
    if avail_mb < 120 or (total_mb <= 1024 and avail_mb < 180):
        return {
            "mode": "primary",
            "max_symbols": 1,
            "reason": "avail_critical_%dMB" % avail_mb,
            "avail_mb": avail_mb,
            "rss_mb": rss,
            "soft_rss_mb": soft,
        }
    if avail_mb < 280 or rss > soft * 0.85 or total_mb <= 1024:
        return {
            "mode": "anchor",
            "max_symbols": 3,
            "reason": "low_ram_anchor_only_avail_%dMB" % avail_mb,
            "avail_mb": avail_mb,
            "rss_mb": rss,
            "soft_rss_mb": soft,
        }
    if avail_mb < 500:
        return {
            "mode": "chunked",
            "max_symbols": 12,
            "reason": "moderate_ram_chunk12_avail_%dMB" % avail_mb,
            "avail_mb": avail_mb,
            "rss_mb": rss,
            "soft_rss_mb": soft,
        }
    return {
        "mode": "full",
        "max_symbols": 38,
        "reason": "ram_ok_avail_%dMB" % avail_mb,
        "avail_mb": avail_mb,
        "rss_mb": rss,
        "soft_rss_mb": soft,
    }


def ensure_headroom(min_avail_mb=100, label=""):
    """GC if needed; return False if still critically low."""
    total, avail = read_meminfo_mb()
    rss = process_rss_mb()
    soft = soft_rss_limit_mb(total)
    if avail < min_avail_mb or rss > soft:
        force_release(label or "ensure_headroom")
        total, avail = read_meminfo_mb()
        rss = process_rss_mb()
    ok = avail >= max(80, int(min_avail_mb * 0.7))
    return {
        "ok": ok,
        "avail_mb": avail,
        "total_mb": total,
        "rss_mb": rss,
        "soft_rss_mb": soft,
        "ts": time.time(),
    }


ANCHOR_SYMBOLS = (
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
)


def resolve_anchor_symbols(matrix_syms, primary=None):
    """BTC/ETH/SOL ∩ matrix, always include primary first."""
    matrix_syms = [str(s) for s in (matrix_syms or []) if s]
    out = []
    seen = set()
    if primary:
        p = str(primary)
        out.append(p)
        seen.add(p)
    for a in ANCHOR_SYMBOLS:
        if a in seen:
            continue
        if a in matrix_syms or not matrix_syms:
            out.append(a)
            seen.add(a)
        if len(out) >= 3:
            break
    # If matrix lacks anchors, keep primary + first others up to 3
    if len(out) < 3:
        for s in matrix_syms:
            if s in seen:
                continue
            out.append(s)
            seen.add(s)
            if len(out) >= 3:
                break
    return out


def select_matrix_subset(matrix_syms, primary=None, budget=None):
    """Apply RAM budget to matrix symbol list."""
    budget = budget or matrix_budget()
    mode = budget.get("mode") or "anchor"
    max_n = int(budget.get("max_symbols") or 3)
    matrix_syms = [str(s) for s in (matrix_syms or []) if s]
    if mode == "primary" or max_n <= 1:
        return ([str(primary)] if primary else matrix_syms[:1]), budget
    if mode in ("anchor",) or max_n <= 3:
        return resolve_anchor_symbols(matrix_syms, primary)[:max_n], budget
    # chunked / full — primary first then rest capped
    out = []
    seen = set()
    if primary:
        out.append(str(primary))
        seen.add(str(primary))
    for s in matrix_syms:
        if s in seen:
            continue
        out.append(s)
        seen.add(s)
        if len(out) >= max_n:
            break
    return out, budget
