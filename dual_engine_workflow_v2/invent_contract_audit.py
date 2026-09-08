# -*- coding: utf-8 -*-
"""Invent contract consistency audit: seed ↔ geometry gate ↔ lock ↔ loop order.

Catches the class of bugs where production defaults sit outside escape-proof
bounds, or identity is forced *after* the gate (illegal values can enqueue).
"""
from __future__ import print_function

import ast
import json
import os
import re
from pathlib import Path

from dual_engine_workflow_v2.timing_stage import (
    LOCK_KEYS_HARD as TS_LOCK_HARD,
    RR_ATR_MAX,
    RR_ATR_MIN,
    RR_XWIN_MAX,
    RR_XWIN_MIN,
    rr_geometry_bounds_ok,
)

ROOT = Path(__file__).resolve().parents[1]


def _rel(path):
    p = Path(path)
    try:
        return str(p.resolve().relative_to(ROOT))
    except Exception:
        return str(p)


# Production invent surfaces (must stay inside RR geometry bounds).
PROD_LITERAL_GLOBS = (
    "scripts/kimi_thin_recipe_loop.py",
    "dual_engine_workflow_v2/thin_create_policy.py",
    "dual_engine_workflow_v2/timing_stage.py",
    "auto_trade/dual_engine/sole_creation_runs/kimi_thin_hub_*_inherit_seeds.example.json",
)

_XWIN_LIT = re.compile(r"""['\"]xwin['\"]\s*[:=]\s*([0-9.]+)""")
_ATR_LIT = re.compile(r"""['\"]atr['\"]\s*[:=]\s*([0-9.]+)""")


def _repo_files():
    out = []
    for pat in PROD_LITERAL_GLOBS:
        out.extend(sorted(ROOT.glob(pat)))
    # de-dupe
    seen = set()
    uniq = []
    for p in out:
        rp = str(p.resolve())
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append(p)
    return uniq


def audit_literal_bounds(paths=None):
    """Every xwin/atr literal in invent production files must pass RR bounds."""
    findings = []
    for path in (paths or _repo_files()):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            findings.append({
                "code": "read_fail",
                "path": str(path),
                "detail": str(exc)[:160],
            })
            continue
        for kind, pat, lo, hi in (
            ("xwin", _XWIN_LIT, RR_XWIN_MIN, RR_XWIN_MAX),
            ("atr", _ATR_LIT, RR_ATR_MIN, RR_ATR_MAX),
        ):
            for m in pat.finditer(text):
                try:
                    val = float(m.group(1))
                except Exception:
                    continue
                if val < lo or val > hi:
                    line = text[: m.start()].count("\n") + 1
                    findings.append({
                        "code": "literal_out_of_bounds",
                        "path": _rel(path),
                        "line": line,
                        "field": kind,
                        "value": val,
                        "lo": lo,
                        "hi": hi,
                    })
    return findings


def audit_recipe_dict(recipe, source):
    ok, err = rr_geometry_bounds_ok(recipe or {})
    if ok:
        return None
    return {
        "code": "recipe_out_of_bounds",
        "source": source,
        "xwin": (recipe or {}).get("xwin"),
        "atr": (recipe or {}).get("atr"),
        "detail": (err or "")[:160],
    }


def audit_inherit_json(path):
    findings = []
    p = Path(path)
    if not p.exists():
        return [{"code": "inherit_missing", "path": str(p)}]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return [{"code": "inherit_json_fail", "path": str(p), "detail": str(exc)[:160]}]
    if not isinstance(data, dict):
        return [{"code": "inherit_not_object", "path": str(p)}]
    for lane, rec in data.items():
        if str(lane).startswith("_"):
            continue
        if not isinstance(rec, dict):
            continue
        hit = audit_recipe_dict(rec, "%s:%s" % (p, lane))
        if hit:
            findings.append(hit)
    return findings


def audit_module_seeds():
    """Import route reseeds + AST SEED_* tables; geometry must pass."""
    findings = []
    try:
        from dual_engine_workflow_v2 import thin_create_policy as tcp
    except Exception as exc:
        return [{"code": "tcp_import_fail", "detail": str(exc)[:160]}]

    base = {
        "family": "xu_long",
        "symbol": "TEST-USDT-SWAP",
        "timeframe": "1h",
        "held": 28,
        "xwin": 8.0,
        "atr": 2.0,
        "hold": 10,
        "timing": [],
        "route": "ema_osc",
    }
    for route in (
        "ema_osc", "channel", "ma_family", "momentum",
        "mean_revert", "vol_confirm", "mtf_filter",
    ):
        state = {
            "tf": "1h",
            "symbol": "TEST-USDT-SWAP",
            "route": "ema_osc",
            "filter_tfs": [],
            "route_rr_index": 0,
            "route_cooldowns": {},
        }
        try:
            out = tcp.switch_route_contract(state, dict(base), new_route=route)
        except Exception as exc:
            findings.append({
                "code": "route_reseed_exc",
                "route": route,
                "detail": str(exc)[:160],
            })
            continue
        seed = (out or {}).get("recipe_seed") if isinstance(out, dict) else None
        if isinstance(seed, dict):
            hit = audit_recipe_dict(seed, "tcp_route_seed:%s" % route)
            if hit:
                findings.append(hit)

    loop_path = ROOT / "scripts" / "kimi_thin_recipe_loop.py"
    if loop_path.exists():
        tree = ast.parse(loop_path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for t in node.targets:
                if not isinstance(t, ast.Name) or not t.id.startswith("SEED_"):
                    continue
                if not isinstance(node.value, ast.Dict):
                    continue
                recipe = {}
                for k, v in zip(node.value.keys, node.value.values):
                    key = None
                    if isinstance(k, ast.Str):
                        key = k.s
                    elif hasattr(ast, "Constant") and isinstance(k, ast.Constant):
                        key = k.value
                    if key not in ("xwin", "atr", "held", "hold", "z"):
                        continue
                    if isinstance(v, ast.Num):
                        recipe[key] = v.n
                    elif hasattr(ast, "Constant") and isinstance(v, ast.Constant):
                        if isinstance(v.value, (int, float)):
                            recipe[key] = v.value
                hit = audit_recipe_dict(recipe, "ast:%s" % t.id)
                if hit:
                    findings.append(hit)
    return findings


def audit_loop_order(loop_path=None):
    """Seed identity force must run before rr_geometry_bounds_ok — unless FREE_CREATE."""
    path = Path(loop_path or (ROOT / "scripts" / "kimi_thin_recipe_loop.py"))
    text = path.read_text(encoding="utf-8")
    gate_idxs = [
        m.start() for m in re.finditer(r"rr_geometry_bounds_ok\(\s*recipe\s*\)", text)
    ]
    if not gate_idxs:
        return [{
            "code": "missing_geometry_gate_call",
            "path": _rel(path),
        }]
    # Free-create mode: no seed overwrite weld; geometry gate alone is enough.
    if "def _free_create" in text or "FREE_CREATE" in text:
        if "never overwrite Kimi recipe with lane seed" in text or "_free_create()" in text:
            return []
    force_idx = text.find("Force lane seed identity")
    if force_idx < 0:
        force_idx = text.find("force seed identity")
    if force_idx < 0:
        return []
    bad = [i for i in gate_idxs if i < force_idx]
    if bad:
        return [{
            "code": "seed_force_after_geometry_gate",
            "path": _rel(path),
            "force_idx": force_idx,
            "early_gate_idx": bad[0],
            "detail": "Illegal: geometry check precedes seed identity force",
        }]
    return []


def audit_lock_parity():
    """timing_stage and thin_create_policy hard-lock key sets must match."""
    findings = []
    try:
        from dual_engine_workflow_v2 import thin_create_policy as tcp
        tcp_hard = tuple(getattr(tcp, "LOCK_KEYS_HARD", ()) or ())
    except Exception as exc:
        return [{"code": "tcp_lock_import_fail", "detail": str(exc)[:160]}]
    a, b = set(TS_LOCK_HARD), set(tcp_hard)
    if a != b:
        findings.append({
            "code": "lock_hard_parity",
            "timing_stage": sorted(a),
            "thin_create_policy": sorted(b),
            "only_ts": sorted(a - b),
            "only_tcp": sorted(b - a),
        })
    # Hard-locked geometry fields that the gate checks must be named.
    for field in ("xwin", "atr"):
        if field not in a and field not in b:
            findings.append({
                "code": "lock_missing_gated_field",
                "field": field,
            })
    return findings


def audit_locked_seed_vs_gate(seeds):
    """If xwin is hard-locked, seed xwin must already be inside the gate."""
    findings = []
    hard = set(TS_LOCK_HARD)
    try:
        from dual_engine_workflow_v2 import thin_create_policy as tcp
        hard |= set(getattr(tcp, "LOCK_KEYS_HARD", ()) or ())
    except Exception:
        pass
    if "xwin" not in hard:
        return findings
    for name, rec in (seeds or {}).items():
        if not isinstance(rec, dict):
            continue
        hit = audit_recipe_dict(rec, "locked_seed:%s" % name)
        if hit:
            hit["code"] = "locked_seed_outside_gate"
            findings.append(hit)
    return findings


def collect_example_seeds():
    seeds = {}
    for p in ROOT.glob(
        "auto_trade/dual_engine/sole_creation_runs/kimi_thin_hub_*_inherit_seeds.example.json"
    ):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for lane, rec in data.items():
            if str(lane).startswith("_") or not isinstance(rec, dict):
                continue
            seeds["%s:%s" % (p.name, lane)] = rec
    return seeds


def collect_live_inherit_seeds(extra_paths=None):
    paths = list(extra_paths or [])
    env = os.environ.get("KDH_THIN_INHERIT_SEEDS") or ""
    if env:
        paths.append(env)
    for name in ("a", "b"):
        paths.append(
            "/root/auto_trade/dual_engine/sole_creation_runs/"
            "kimi_thin_hub_%s_inherit_seeds.json" % name
        )
    seeds = {}
    findings = []
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            continue
        findings.extend(audit_inherit_json(p))
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            for lane, rec in data.items():
                if isinstance(rec, dict) and not str(lane).startswith("_"):
                    seeds["live:%s:%s" % (p.name, lane)] = rec
    return seeds, findings


def run_audit(include_live_inherit=None):
    """Return {ok, findings, summary}. include_live_inherit: None=auto if /root exists."""
    if include_live_inherit is None:
        include_live_inherit = Path("/root/scripts/kimi_thin_recipe_loop.py").exists()

    findings = []
    findings.extend(audit_literal_bounds())
    findings.extend(audit_loop_order())
    findings.extend(audit_lock_parity())
    findings.extend(audit_module_seeds())

    example_seeds = collect_example_seeds()
    findings.extend(audit_locked_seed_vs_gate(example_seeds))
    for name, rec in example_seeds.items():
        hit = audit_recipe_dict(rec, name)
        if hit:
            findings.append(hit)

    live_findings = []
    if include_live_inherit:
        _seeds, live_findings = collect_live_inherit_seeds()
        findings.extend(live_findings)
        findings.extend(audit_locked_seed_vs_gate(_seeds))

    # Drop duplicate inherit_missing for optional /root when not on VPS
    cleaned = []
    for f in findings:
        if f.get("code") == "inherit_missing" and not include_live_inherit:
            continue
        cleaned.append(f)

    by = {}
    for f in cleaned:
        by[f.get("code") or "?"] = by.get(f.get("code") or "?", 0) + 1
    return {
        "ok": not cleaned,
        "findings": cleaned,
        "summary": by,
        "bounds": {
            "xwin": [RR_XWIN_MIN, RR_XWIN_MAX],
            "atr": [RR_ATR_MIN, RR_ATR_MAX],
        },
    }


def main(argv=None):
    import sys
    argv = list(argv or sys.argv[1:])
    live = None
    if "--live" in argv:
        live = True
    if "--no-live" in argv:
        live = False
    pack = run_audit(include_live_inherit=live)
    print(json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if pack.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
