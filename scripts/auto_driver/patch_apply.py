# -*- coding: utf-8 -*-
"""Apply AI-proposed patches onto a strategy candidate pack (in-memory)."""
from __future__ import print_function

import copy
import re


def deep_copy_pack(pack):
    return copy.deepcopy(pack)


def snapshot_pack(pack):
    return copy.deepcopy(pack)


def restore_pack(snapshot):
    return copy.deepcopy(snapshot)


def _parse_path(path):
    tokens = []
    for part in re.findall(r"[^\.\[\]]+|\[\d+\]", str(path)):
        if part.startswith("[") and part.endswith("]"):
            tokens.append(("idx", int(part[1:-1])))
        else:
            tokens.append(("key", part))
    return tokens


def get_by_path(obj, path):
    cur = obj
    for kind, val in _parse_path(path):
        if kind == "key":
            if not isinstance(cur, dict):
                raise KeyError("path parent not dict: %s" % path)
            cur = cur[val]
        else:
            if not isinstance(cur, list):
                raise KeyError("path parent not list: %s" % path)
            cur = cur[val]
    return cur


def set_by_path(obj, path, value):
    tokens = _parse_path(path)
    if not tokens:
        raise KeyError("empty path")
    cur = obj
    for i, (kind, val) in enumerate(tokens[:-1]):
        nxt = tokens[i + 1]
        if kind == "key":
            if not isinstance(cur, dict):
                raise KeyError("cannot traverse key on non-dict: %s" % path)
            if val not in cur or cur[val] is None:
                cur[val] = [] if nxt[0] == "idx" else {}
            cur = cur[val]
        else:
            if not isinstance(cur, list):
                raise KeyError("cannot traverse idx on non-list: %s" % path)
            cur = cur[val]
    last_kind, last_val = tokens[-1]
    if last_kind == "key":
        if not isinstance(cur, dict):
            raise KeyError("cannot set key on non-dict: %s" % path)
        cur[last_val] = value
    else:
        if not isinstance(cur, list):
            raise KeyError("cannot set index on non-list: %s" % path)
        while len(cur) <= last_val:
            cur.append(None)
        cur[last_val] = value
    return obj


def sanitize_patches(patches):
    """Clamp known DSL hard bounds so AI patches do not bounce on validate.

    Hard bounds (prod DSL validator):
      - atr_trailing / partial_tp_atr n_atr ∈ [2.5, 5.0]
        (partial_tp_atr may use ~2.0; clamp trail-like paths only when >5 or <2.0
         for trail; see path heuristics below)
      - swing lookback ∈ [5, 60]
      - protective stop_pct ∈ (0, 0.009]
    """
    out = []
    for p in patches or []:
        if not isinstance(p, dict):
            continue
        q = dict(p)
        path = str(q.get("path") or "")
        op = str(q.get("op") or "").lower()
        if op in ("set", "set_path", "add") and "value" in q:
            val = q.get("value")
            pl = path.lower()
            if pl.endswith(".n_atr") or pl.endswith("/n_atr") or pl.endswith("n_atr"):
                try:
                    v = float(val)
                    # atr_trailing hard max 5.0; allow partial_tp down to 2.0
                    if "partial" in pl:
                        q["value"] = max(2.0, min(5.0, v))
                    else:
                        q["value"] = max(2.5, min(5.0, v))
                except Exception:
                    pass
            if pl.endswith(".lookback") or pl.endswith("/lookback") or pl.endswith("lookback"):
                try:
                    q["value"] = max(5, min(60, int(float(val))))
                except Exception:
                    pass
            if "stop_pct" in pl or pl.endswith(".stop_loss_pct"):
                try:
                    v = abs(float(val))
                    q["value"] = max(0.001, min(0.009, v))
                except Exception:
                    pass
        if op == "replace_exit" and isinstance(q.get("exit"), dict):
            q["exit"] = _sanitize_exit_tree(q["exit"])
        if op in ("replace_dsl", "set_dsl", "merge_dsl") and isinstance(q.get("dsl"), dict):
            dsl = deep_copy_pack(q["dsl"])
            if isinstance(dsl.get("exit"), dict):
                dsl["exit"] = _sanitize_exit_tree(dsl["exit"])
            q["dsl"] = dsl
        out.append(q)
    return out


def _sanitize_exit_tree(exit_):
    out = deep_copy_pack(exit_)
    any_list = out.get("any")
    if not isinstance(any_list, list):
        return out
    for item in any_list:
        if not isinstance(item, dict):
            continue
        eop = str(item.get("exit_op") or "")
        if "n_atr" in item:
            try:
                v = float(item["n_atr"])
                if eop == "partial_tp_atr":
                    item["n_atr"] = max(2.0, min(5.0, v))
                else:
                    item["n_atr"] = max(2.5, min(5.0, v))
            except Exception:
                pass
        if "lookback" in item:
            try:
                item["lookback"] = max(5, min(60, int(float(item["lookback"]))))
            except Exception:
                pass
    return out


def apply_patches(pack, patches, direction="long"):
    out = deep_copy_pack(pack)
    applied = []
    errors = []
    patches = sanitize_patches(patches)
    for i, patch in enumerate(patches or []):
        if not isinstance(patch, dict):
            errors.append({"i": i, "error": "patch_not_object"})
            continue
        op = str(patch.get("op") or "").strip().lower()
        try:
            if op in ("set", "set_path"):
                path = patch.get("path")
                if not path:
                    raise ValueError("set requires path")
                set_by_path(out, path, patch.get("value"))
                # convenience: dsl.* also writes active direction leaf
                if str(path).startswith("dsl.") or path == "dsl":
                    active = out.get("dsl")
                    if isinstance(active, dict):
                        _write_active_dsl(out, direction, active)
                applied.append({"op": "set", "path": path, "value": patch.get("value")})
            elif op in ("replace_dsl", "set_dsl"):
                dsl = patch.get("dsl")
                if not isinstance(dsl, dict):
                    raise ValueError("replace_dsl requires dsl object")
                _write_active_dsl(out, direction, dsl)
                applied.append({"op": "replace_dsl", "keys": list(dsl.keys())[:20]})
            elif op == "merge_dsl":
                dsl = patch.get("dsl") or {}
                if not isinstance(dsl, dict):
                    raise ValueError("merge_dsl requires dsl object")
                cur = _read_active_dsl(out, direction) or {}
                cur = deep_copy_pack(cur)
                cur.update(dsl)
                _write_active_dsl(out, direction, cur)
                applied.append({"op": "merge_dsl", "keys": list(dsl.keys())[:20]})
            elif op == "replace_entry":
                entry = patch.get("entry")
                if not isinstance(entry, dict):
                    raise ValueError("replace_entry requires entry object")
                cur = _read_active_dsl(out, direction) or {}
                cur = deep_copy_pack(cur)
                cur["entry"] = entry
                _write_active_dsl(out, direction, cur)
                applied.append({"op": "replace_entry"})
            elif op == "replace_exit":
                exit_ = patch.get("exit")
                if not isinstance(exit_, dict):
                    raise ValueError("replace_exit requires exit object")
                cur = _read_active_dsl(out, direction) or {}
                cur = deep_copy_pack(cur)
                cur["exit"] = exit_
                _write_active_dsl(out, direction, cur)
                applied.append({"op": "replace_exit"})
            elif op == "merge_mechanism_spec":
                spec = patch.get("mechanism_spec") or patch.get("spec") or {}
                if not isinstance(spec, dict):
                    raise ValueError("merge_mechanism_spec requires object")
                base = deep_copy_pack(out.get("mechanism_spec") or {})
                base.update(spec)
                out["mechanism_spec"] = base
                applied.append({"op": "merge_mechanism_spec", "keys": list(spec.keys())[:20]})
            elif op == "rename_family":
                fam = patch.get("mechanism_family") or patch.get("value")
                if not fam:
                    raise ValueError("rename_family requires mechanism_family")
                spec = deep_copy_pack(out.get("mechanism_spec") or {})
                old = spec.get("mechanism_family")
                spec["mechanism_family"] = str(fam)
                if patch.get("mechanism_name"):
                    spec["mechanism_name"] = str(patch.get("mechanism_name"))
                mid = spec.get("mechanism_id") or ""
                if mid and patch.get("bump_mechanism_id", True):
                    spec["mechanism_id"] = "%s_ad" % mid
                out["mechanism_spec"] = spec
                applied.append({"op": "rename_family", "from": old, "to": fam})
            elif op == "set_max_hold_bars":
                n = int(patch.get("value"))
                cur = _read_active_dsl(out, direction) or {}
                cur = deep_copy_pack(cur)
                cur["max_hold_bars"] = n
                _write_active_dsl(out, direction, cur)
                applied.append({"op": "set_max_hold_bars", "value": n})
            elif op == "noop":
                applied.append({"op": "noop", "note": patch.get("note")})
            else:
                errors.append({"i": i, "error": "unknown_op", "op": op})
        except Exception as exc:
            errors.append({"i": i, "error": str(exc), "op": op, "patch": patch})
    active = _read_active_dsl(out, direction)
    if isinstance(active, dict):
        out["dsl"] = deep_copy_pack(active)
        if str(direction).lower() == "short":
            out["dsl_short"] = deep_copy_pack(active)
        else:
            out["dsl_long"] = deep_copy_pack(active)
    return out, applied, errors


def _read_active_dsl(pack, direction):
    if str(direction).lower() == "short":
        return pack.get("dsl_short") or pack.get("dsl")
    return pack.get("dsl_long") or pack.get("dsl")


def _write_active_dsl(pack, direction, dsl):
    dsl = deep_copy_pack(dsl)
    pack["dsl"] = dsl
    if str(direction).lower() == "short":
        pack["dsl_short"] = deep_copy_pack(dsl)
    else:
        pack["dsl_long"] = deep_copy_pack(dsl)


def bump_family_for_kb(pack, iteration):
    """Bump mechanism_family to a fresh *_adN that is strictly newer than current.

    Important: if pack is already `foo_ad1` and iteration==1, naive
    `base + _ad{iteration}` would be a no-op and re-hit kb_blocked.
    Always choose N = max(current_suffix+1, int(iteration)).
    """
    out = deep_copy_pack(pack)
    spec = deep_copy_pack(out.get("mechanism_spec") or {})
    fam = str(spec.get("mechanism_family") or "strategy")
    m = re.search(r"_ad(\d+)$", fam)
    cur_n = int(m.group(1)) if m else 0
    try:
        want = int(iteration)
    except Exception:
        want = cur_n + 1
    new_n = max(cur_n + 1, want)
    base = re.sub(r"_ad\d+$", "", fam)
    new_fam = "%s_ad%d" % (base, new_n)
    spec["mechanism_family"] = new_fam
    if spec.get("mechanism_name"):
        name = re.sub(r"_ad\d+$", "", str(spec.get("mechanism_name")))
        spec["mechanism_name"] = "%s_ad%d" % (name, new_n)
    mid = str(spec.get("mechanism_id") or "mech")
    mid = re.sub(r"_ad\d+$", "", mid)
    spec["mechanism_id"] = "%s_ad%d" % (mid, new_n)
    nn = list(spec.get("non_negotiable_rules") or [])
    tag = "auto_driver_family_lineage_bump"
    if tag not in nn:
        nn.append(tag)
    spec["non_negotiable_rules"] = nn
    out["mechanism_spec"] = spec
    return out, new_fam
