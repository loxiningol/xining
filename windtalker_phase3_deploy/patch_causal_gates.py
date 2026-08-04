#!/usr/bin/env python3
# Patch Phase3 orchestrator causal gates + resume from 3F
from pathlib import Path
import shutil

p = Path("/root/auto_trade/windtalker_phase3/scripts/windtalker_phase3_orchestrator.py")
t = p.read_text()

repls = []

repls.append((
'''        causal["causal_fidelity_pass"] = (
            cpass >= 4
            and r["structural"].get("structural_pass")
            and r["structural"].get("real_new_data_present")
            and not r["structural"].get("ohlc_template_collapse")
        )''',
'''        min_entries = int((causal.get("base_entries") or 0) >= 5)
        causal["min_signal_activity_pass"] = bool(min_entries)
        causal["causal_fidelity_pass"] = (
            cpass >= 4
            and bool(min_entries)
            and r["structural"].get("structural_pass")
            and r["structural"].get("real_new_data_present")
            and not r["structural"].get("ohlc_template_collapse")
        )'''
))

repls.append((
'''{"id": "e_fund", "left": {"feature": "funding_z20"}, "op": "gt", "right": {"value": 0.5}},
                {"id": "e_basis", "left": {"feature": "basis_bps"}, "op": "gt", "right": {"value": 1.0}},
                {"id": "e_turn", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}},''',
'''{"id": "e_fund", "left": {"feature": "funding_z20"}, "op": "gt", "right": {"value": 0.0}},
                {"id": "e_basis", "left": {"feature": "basis_bps"}, "op": "gt", "right": {"value": 0.0}},
                {"id": "e_turn", "left": {"feature": "close"}, "op": "lt", "right": {"feature": "open"}},'''
))

repls.append((
'''    for col in ("oi_delta_pct", "taker_imbalance", "funding_rate"):
        if col in frame_eo:
            frame_eo[col] = [(-x if math.isfinite(x) else x) for x in frame_eo[col]]''',
'''    for col in ("oi_delta_pct", "taker_imbalance", "funding_rate", "oi_z20", "oi_crowding",
                "funding_z20", "basis_bps", "basis_z20", "taker_imbalance_z20"):
        if col in frame_eo:
            frame_eo[col] = [(-x if math.isfinite(x) else x) for x in frame_eo[col]]
    for col in research_cols:
        if col in frame_eo:
            xs = list(frame_eo[col])
            frame_eo[col] = list(reversed(xs))'''
))

repls.append((
'''    results["event_order_destroy"] = {
        "entries": len(eo_entries),
        "overlap_with_base": round(eo_overlap, 4),
        "pass": eo_overlap <= 0.75 or len(eo_entries) != len(base_entries),
    }''',
'''    if len(base_entries) >= 5:
        eo_pass = eo_overlap <= 0.75
    else:
        eo_pass = False
    results["event_order_destroy"] = {
        "entries": len(eo_entries),
        "overlap_with_base": round(eo_overlap, 4),
        "pass": eo_pass,
    }'''
))

repls.append((
'''    results["ablation"] = {
        "entries": len(ab_entries),
        "pass": len(ab_entries) <= max(1, int(0.6 * max(len(base_entries), 1))),
        "note": "entries must drop when research features removed",
    }''',
'''    if len(base_entries) < 5:
        ab_pass = False
    else:
        ab_pass = len(ab_entries) <= int(0.6 * len(base_entries))
    results["ablation"] = {
        "entries": len(ab_entries),
        "pass": ab_pass,
        "note": "entries must drop when research features removed; requires >=5 base entries",
    }'''
))

repls.append((
'''    results["random_proxy"] = {
        "entries": len(rp_entries),
        "pnl": rp_pnl,
        "overlap_with_base": round(overlap, 4),
        "pass": overlap <= 0.7 or degrade,
    }''',
'''    if len(base_entries) < 5:
        rp_pass = False
    else:
        rp_pass = overlap <= 0.7 or degrade
    results["random_proxy"] = {
        "entries": len(rp_entries),
        "pnl": rp_pnl,
        "overlap_with_base": round(overlap, 4),
        "pass": rp_pass,
    }'''
))

for i, (a, b) in enumerate(repls):
    if a not in t:
        raise SystemExit("missing anchor %s" % i)
    t = t.replace(a, b)

p.write_text(t)
print("patched")

R = Path("/root/auto_trade/windtalker_phase3")
for name in ["3F_3G_done.json", "3H_done.json", "3I_done.json"]:
    fp = R / "checkpoints" / name
    if fp.exists():
        fp.unlink()
        print("removed", name)
for name in [
    "DONE.json",
    "WINDTALKER_PHASE3_PROBES_SUMMARY.json",
    "WINDTALKER_PHASE3_final_report.md",
    "WINDTALKER_PHASE3_PRODUCTION_ISOLATION.json",
]:
    fp = R / name
    if fp.exists():
        fp.unlink()
        print("removed artifact", name)
pr = R / "probes"
if pr.exists():
    shutil.rmtree(pr)
pr.mkdir(parents=True, exist_ok=True)
print("ready")
