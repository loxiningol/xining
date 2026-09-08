# -*- coding: utf-8 -*-
"""Mac research host: pull VPS thin-hub recipes, run isolated_cap, push results.

Never mounts. Never calls Kimi. VPS stays light.

Parallel compute: claim recipes into /tmp/kdh_thin*/inflight, then evaluate up to
N jobs in a process pool (default 8). Claim is atomic so multiple workers or
agents cannot double-run the same recipe. micro_search fans out variants onto
the same pool (see micro_fanout logs).

  VECTOR_ROOT=$PWD/.research_vector PYTHONPATH=$PWD:$PWD/scripts \\
  python3 scripts/research_host_eval_worker.py --workers 8
"""
from __future__ import print_function

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from kdh_load_create_mod import load_kdh_mod  # noqa: E402

DEFAULT_KEY = "/Users/lele/Documents/Codex/2026-07-12/ru-g/work/ssh/vultr_codex_ed25519"
DEFAULT_HOST = "admin@47.81.25.235"
# Comma-separated VPS thin roots. Default serves hub-a + hub-b in parallel.
DEFAULT_ROOTS = "/tmp/kdh_thin,/tmp/kdh_thin_b"
LOCAL_Q = ROOT / ".research_thin" / "queue"
LOCAL_R = ROOT / ".research_thin" / "results"
LOG = ROOT / "research_thin_eval.jsonl"

# Set in process-pool workers via initializer.
_POOL_MOD = None


def _thin_roots(raw=None):
    text = raw if raw is not None else (
        os.environ.get("KDH_THIN_ROOTS")
        or os.environ.get("KDH_THIN_ROOT")
        or DEFAULT_ROOTS
    )
    out = []
    seen = set()
    for part in str(text or "").split(","):
        root = part.strip().rstrip("/")
        if not root or root in seen:
            continue
        seen.add(root)
        out.append({
            "root": root,
            "queue": "%s/queue" % root,
            "inflight": "%s/inflight" % root,
            "results": "%s/results" % root,
        })
    return out or [{
        "root": "/tmp/kdh_thin",
        "queue": "/tmp/kdh_thin/queue",
        "inflight": "/tmp/kdh_thin/inflight",
        "results": "/tmp/kdh_thin/results",
    }]


def _finished(returncode, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def _ssh(host, key, remote_cmd, timeout=60):
    cmd = [
        "ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
        host, remote_cmd,
    ]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return _finished(124, "ssh_timeout:%s" % exc)
    except OSError as exc:
        return _finished(125, "ssh_os:%s" % exc)


def _scp_from(host, key, remote, local, timeout=60):
    local.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "scp", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
        "%s:%s" % (host, remote), str(local),
    ]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return _finished(124, "scp_timeout:%s" % exc)
    except OSError as exc:
        return _finished(125, "scp_os:%s" % exc)


def _scp_to(host, key, local, remote, timeout=60):
    cmd = [
        "scp", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
        str(local), "%s:%s" % (host, remote),
    ]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        return _finished(124, "scp_timeout:%s" % exc)
    except OSError as exc:
        return _finished(125, "scp_os:%s" % exc)


def _list_recipe_dir(host, key, remote_dir):
    r = _ssh(
        host, key,
        "sudo ls -1 %s/*.recipe.json 2>/dev/null || true" % remote_dir,
        timeout=25,
    )
    names = []
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.endswith(".recipe.json"):
            names.append(line)
    if names:
        return names
    rc = getattr(r, "returncode", None)
    if rc not in (0, None) and not (r.stdout or "").strip():
        _emit({
            "phase": "list_fail",
            "dir": remote_dir,
            "error": (r.stderr or "ssh_fail")[:200],
            "rc": rc,
        })
    return []


def _list_queue(host, key, ns):
    return _list_recipe_dir(host, key, ns["queue"])


def _list_inflight(host, key, ns):
    return _list_recipe_dir(host, key, ns["inflight"])


def _claim_one(host, key, ns, remote_queue_path):
    """Atomically move queue recipe -> inflight. Returns inflight path or None."""
    base = Path(remote_queue_path).name
    dest = "%s/%s" % (ns["inflight"], base)
    r = _ssh(
        host, key,
        "sudo mkdir -p %s %s && sudo mv %s %s && echo CLAIMED || echo BUSY"
        % (ns["queue"], ns["inflight"], remote_queue_path, dest),
        timeout=25,
    )
    out = (r.stdout or "").strip()
    if "CLAIMED" in out:
        return dest
    return None


def _emit(row):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    print(json.dumps(row, ensure_ascii=False, default=str)[:500], flush=True)


def _score_cap(cap_row):
    return (
        1 if cap_row.get("hit_floor") else 0,
        float(cap_row.get("C_week_pct") or -999),
        float(cap_row.get("C_week_oos_pct") or -999),
        float(cap_row.get("E") or cap_row.get("E_path") or -9),
        -(float(cap_row.get("hitch") or 9)),
        int(cap_row.get("n") or 0),
    )


def _micro_search_parallel(mod, payload, pool=None, workers=8):
    """Scan timing leaf variants in parallel; return micro_search_done payload."""
    from dual_engine_workflow_v2.timing_stage import timing_leaf_variants, classify_stage
    from concurrent.futures import ThreadPoolExecutor

    recipe = payload.get("recipe") or {}
    lane = payload.get("lane") or "backup"
    job_id = payload.get("id") or "unknown"
    base = dict(recipe)
    diagnosis = payload.get("diagnosis") or {}
    variants = timing_leaf_variants(base.get("timing") or [], diagnosis, limit=32)
    workers = max(1, min(int(workers or 1), 16, max(1, len(variants))))

    def _eval_timing(timing):
        trial = dict(base)
        trial["timing"] = timing
        cand, err = mod._cand_from_recipe(trial, lane) if pool is None else (None, None)
        if pool is None:
            if err:
                return None
            try:
                cap_row = mod.isolated_cap(cand, {"ir": mod._ir_payload(cand)})
            except Exception:
                return None
            if not cap_row or not cap_row.get("ok"):
                return None
            return {
                "recipe": mod._recipe_snap(trial),
                "cap": {
                    "C_week_pct": cap_row.get("C_week_pct"),
                    "C_week_oos_pct": cap_row.get("C_week_oos_pct"),
                    "n": cap_row.get("n"),
                    "hitch": cap_row.get("hitch"),
                    "E": cap_row.get("E") or cap_row.get("E_path"),
                    "hit_floor": cap_row.get("hit_floor"),
                    "stage": cap_row.get("stage") or classify_stage(cap_row),
                    "diagnosis": cap_row.get("diagnosis"),
                },
                "score": _score_cap(cap_row),
            }
        # Reuse shared process pool: one normal eval job per variant.
        sub = {
            "id": "%s_v" % job_id,
            "lane": lane,
            "mode": "eval",
            "recipe": trial,
        }
        result = None
        # Caller submits; this branch unused when pool path uses submit below.
        return sub

    best = None
    scanned = 0
    if pool is not None and len(variants) > 1:
        futs = []
        for timing in variants:
            trial = dict(base)
            trial["timing"] = timing
            sub = {
                "id": "%s_v" % job_id,
                "lane": lane,
                "mode": "eval",
                "recipe": trial,
            }
            futs.append((pool.submit(_pool_eval, sub), trial))
        _emit({
            "phase": "micro_fanout",
            "id": job_id,
            "variants": len(futs),
            "workers": workers,
        })
        for fut, trial in futs:
            scanned += 1
            try:
                result = fut.result()
            except Exception:
                continue
            if not result or not result.get("ok"):
                # still count below-floor numeric rows if present
                if not result or result.get("C_week_pct") is None:
                    continue
            cap = {
                "C_week_pct": result.get("C_week_pct"),
                "C_week_oos_pct": result.get("C_week_oos_pct"),
                "n": result.get("n"),
                "hitch": result.get("hitch"),
                "E": result.get("E"),
                "hit_floor": result.get("hit_floor"),
                "stage": result.get("stage") or classify_stage(result),
                "diagnosis": result.get("diagnosis"),
            }
            # Accept rows that have capability numbers even if ok=False/below_floor.
            if cap.get("C_week_pct") is None and not cap.get("hit_floor"):
                continue
            row = {
                "recipe": result.get("recipe") or mod._recipe_snap(trial),
                "cap": cap,
                "score": _score_cap(cap),
            }
            if best is None or row["score"] > best["score"]:
                best = row
    else:
        # Local thread fan-out (workers=1 or single variant).
        def _one_timing(timing):
            trial = dict(base)
            trial["timing"] = timing
            cand, err = mod._cand_from_recipe(trial, lane)
            if err:
                return None
            try:
                cap_row = mod.isolated_cap(cand, {"ir": mod._ir_payload(cand)})
            except Exception:
                return None
            if not cap_row or not cap_row.get("ok"):
                if not cap_row or cap_row.get("C_week_pct") is None:
                    return None
            cap = {
                "C_week_pct": (cap_row or {}).get("C_week_pct"),
                "C_week_oos_pct": (cap_row or {}).get("C_week_oos_pct"),
                "n": (cap_row or {}).get("n"),
                "hitch": (cap_row or {}).get("hitch"),
                "E": (cap_row or {}).get("E") or (cap_row or {}).get("E_path"),
                "hit_floor": (cap_row or {}).get("hit_floor"),
                "stage": (cap_row or {}).get("stage") or classify_stage(cap_row or {}),
                "diagnosis": (cap_row or {}).get("diagnosis"),
            }
            return {
                "recipe": mod._recipe_snap(trial),
                "cap": cap,
                "score": _score_cap(cap),
            }

        if len(variants) <= 1:
            for timing in variants:
                scanned += 1
                row = _one_timing(timing)
                if row is not None and (best is None or row["score"] > best["score"]):
                    best = row
        else:
            with ThreadPoolExecutor(max_workers=workers) as tex:
                futs = [tex.submit(_one_timing, timing) for timing in variants]
                for fut in as_completed(futs):
                    scanned += 1
                    try:
                        row = fut.result()
                    except Exception:
                        continue
                    if row is not None and (best is None or row["score"] > best["score"]):
                        best = row

    out = {
        "id": job_id,
        "ok": best is not None,
        "phase": "micro_search_done",
        "host": "mac_research",
        "mode": "micro_search",
        "scanned": scanned,
        "parallel_workers": workers,
        "best_recipe": None if best is None else best["recipe"],
        "hit_floor": False if best is None else bool(best["cap"].get("hit_floor")),
        "C_week_pct": None if best is None else best["cap"].get("C_week_pct"),
        "C_week_oos_pct": None if best is None else best["cap"].get("C_week_oos_pct"),
        "n": None if best is None else best["cap"].get("n"),
        "hitch": None if best is None else best["cap"].get("hitch"),
        "E": None if best is None else best["cap"].get("E"),
        "stage": None if best is None else best["cap"].get("stage"),
        "diagnosis": None if best is None else best["cap"].get("diagnosis"),
        "symbol": (best or {}).get("recipe", {}).get("symbol") or recipe.get("symbol"),
        "recipe": None if best is None else best["recipe"],
    }
    return out


def eval_job(mod, payload):
    recipe = payload.get("recipe") or {}
    lane = payload.get("lane") or "backup"
    job_id = payload.get("id") or "unknown"
    mode = str(payload.get("mode") or "eval")
    ledger = ROOT / "timing_ledger.jsonl"

    def _one(rec):
        cand, err = mod._cand_from_recipe(rec, lane)
        if err:
            return None, {
                "ok": False,
                "phase": "pair_reject",
                "reject": err,
                "symbol": rec.get("symbol"),
            }
        try:
            cap_row = mod.isolated_cap(cand, {"ir": mod._ir_payload(cand)})
        except Exception as exc:
            return cand, {
                "ok": False,
                "phase": "eval_exc",
                "error": str(exc)[:200],
                "trace": traceback.format_exc()[-240:],
            }
        return cand, cap_row

    if mode == "micro_search":
        # Fallback path when called inside a worker without shared pool fan-out.
        out = _micro_search_parallel(
            mod, payload, pool=None,
            workers=int(os.environ.get("KDH_THIN_WORKERS") or "8"),
        )
        try:
            with ledger.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": time.time(), **out}, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass
        return out

    cand, cap_row = _one(recipe)
    if cand is None:
        cap_row = cap_row or {}
        cap_row["id"] = job_id
        cap_row["host"] = "mac_research"
        return cap_row
    if cap_row.get("phase") == "eval_exc" or cap_row.get("reject"):
        cap_row["id"] = job_id
        cap_row["host"] = "mac_research"
        return cap_row
    out = {
        "id": job_id,
        "ok": bool(cap_row.get("ok")),
        "phase": "hit_floor" if cap_row.get("hit_floor") else "below_floor",
        "cand_id": cand.get("id"),
        "symbol": cand.get("symbol"),
        "timeframe": cand.get("timeframe") or cap_row.get("timeframe") or recipe.get("timeframe"),
        "exec_tf": cand.get("exec_tf") or recipe.get("exec_tf") or cap_row.get("exec_tf"),
        "filter_tfs": cand.get("filter_tfs") or recipe.get("filter_tfs") or cap_row.get("filter_tfs") or [],
        "route": cand.get("route") or recipe.get("route"),
        "mtf_filter": cap_row.get("mtf_filter"),
        "C_week_pct": cap_row.get("C_week_pct"),
        "C_week_oos_pct": cap_row.get("C_week_oos_pct"),
        "n": cap_row.get("n"),
        "n_oos": cap_row.get("n_oos"),
        "hitch": cap_row.get("hitch"),
        "oos_hitch": cap_row.get("oos_hitch"),
        "usable": cap_row.get("usable"),
        "oos_usable": cap_row.get("oos_usable"),
        "hit_floor": bool(cap_row.get("hit_floor")),
        "blockers": cap_row.get("blockers"),
        "oos_blockers": cap_row.get("oos_blockers"),
        "error": cap_row.get("error"),
        "E": cap_row.get("E") or cap_row.get("E_path"),
        "E_path": cap_row.get("E_path"),
        "weekly": cap_row.get("weekly"),
        "diagnosis": cap_row.get("diagnosis"),
        "stage": cap_row.get("stage"),
        "host": "mac_research",
        "recipe": mod._recipe_snap(recipe),
    }
    # Invent lineage (optional; thin hub hypothesis loop)
    if payload.get("parent_id"):
        out["parent_id"] = payload.get("parent_id")
    if payload.get("step_id"):
        out["step_id"] = payload.get("step_id")
    try:
        with ledger.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "at": time.time(), "id": job_id, "stage": out.get("stage"),
                "n": out.get("n"), "hitch": out.get("hitch"), "E": out.get("E"),
                "C_week_pct": out.get("C_week_pct"), "symbol": out.get("symbol"),
                "diagnosis_hint": (out.get("diagnosis") or {}).get("hint"),
            }, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
    return out


def _pool_init(root_str, vector_root, pythonpath):
    global _POOL_MOD, ROOT, LOCAL_Q, LOCAL_R, LOG
    ROOT = Path(root_str)
    LOCAL_Q = ROOT / ".research_thin" / "queue"
    LOCAL_R = ROOT / ".research_thin" / "results"
    LOG = ROOT / "research_thin_eval.jsonl"
    os.environ["VECTOR_ROOT"] = vector_root
    os.environ["PYTHONPATH"] = pythonpath
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from kdh_load_create_mod import load_kdh_mod as _load
    _POOL_MOD = _load()


def _pool_eval(payload):
    return eval_job(_POOL_MOD, payload)


def _pull_payload(host, key, remote_path, job_id):
    base = Path(remote_path).name
    local_in = LOCAL_Q / base
    sc = _scp_from(host, key, remote_path, local_in)
    if sc.returncode != 0:
        cat = _ssh(host, key, "sudo cat %s" % remote_path, timeout=30)
        if cat.returncode != 0 or not cat.stdout:
            _emit({"phase": "pull_fail", "remote": remote_path, "err": (sc.stderr or "")[:160]})
            return None
        local_in.write_text(cat.stdout, encoding="utf-8")
    try:
        return json.loads(local_in.read_text(encoding="utf-8"))
    except Exception as exc:
        _emit({"phase": "bad_json", "remote": remote_path, "error": str(exc)[:120]})
        return None


def _push_result(host, key, job_id, remote_inflight, result, ns):
    local_out = LOCAL_R / ("%s.result.json" % job_id)
    remote_out = "%s/%s.result.json" % (ns["results"], job_id)
    LOCAL_R.mkdir(parents=True, exist_ok=True)
    local_out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    tmp_remote = "/tmp/kdh_thin_push_%s.json" % job_id
    sc2 = None
    for _try in range(5):
        sc2 = _scp_to(host, key, local_out, tmp_remote)
        if sc2.returncode == 0:
            break
        time.sleep(2.0 * (_try + 1))
    if sc2 is None or sc2.returncode != 0:
        _emit({"phase": "push_fail", "id": job_id, "err": ((sc2.stderr if sc2 else "") or "")[:160]})
        return False
    mv = None
    for _try in range(5):
        mv = _ssh(
            host, key,
            "sudo mkdir -p %s && sudo mv %s %s && sudo rm -f %s"
            % (ns["results"], tmp_remote, remote_out, remote_inflight),
        )
        if mv.returncode == 0:
            break
        time.sleep(2.0 * (_try + 1))
    if mv is None or mv.returncode != 0:
        _emit({"phase": "mv_fail", "id": job_id, "err": ((mv.stderr if mv else "") or "")[:160]})
        return False
    _emit({
        "phase": "eval_done",
        "id": job_id,
        "root": ns.get("root"),
        "symbol": result.get("symbol"),
        "timeframe": result.get("timeframe"),
        "C_week_pct": result.get("C_week_pct"),
        "C_week_oos_pct": result.get("C_week_oos_pct"),
        "hit_floor": result.get("hit_floor"),
        "n": result.get("n"),
        "hitch": result.get("hitch"),
        "E": result.get("E"),
        "stage": result.get("stage"),
        "mode": result.get("mode") or "eval",
    })
    return True


def _gather_batch(host, key, limit, roots):
    """Prefer stale inflight (crash recovery), then claim fresh queue items."""
    batch = []
    seen = set()
    for ns in roots:
        if len(batch) >= limit:
            break
        for remote in _list_inflight(host, key, ns):
            if len(batch) >= limit:
                break
            base = Path(remote).name
            job_id = base.replace(".recipe.json", "")
            if job_id in seen:
                continue
            remote_out = "%s/%s.result.json" % (ns["results"], job_id)
            chk = _ssh(host, key, "sudo test -f %s && echo yes || echo no" % remote_out)
            if "yes" in (chk.stdout or ""):
                _ssh(host, key, "sudo rm -f %s" % remote)
                continue
            payload = _pull_payload(host, key, remote, job_id)
            if payload is None:
                continue
            seen.add(job_id)
            batch.append({
                "job_id": job_id,
                "remote_inflight": remote,
                "payload": payload,
                "ns": ns,
            })
            _emit({"phase": "resume_inflight", "id": job_id, "root": ns["root"]})
        for remote in _list_queue(host, key, ns):
            if len(batch) >= limit:
                break
            base = Path(remote).name
            job_id = base.replace(".recipe.json", "")
            if job_id in seen:
                continue
            remote_out = "%s/%s.result.json" % (ns["results"], job_id)
            chk = _ssh(host, key, "sudo test -f %s && echo yes || echo no" % remote_out)
            if "yes" in (chk.stdout or ""):
                _ssh(host, key, "sudo rm -f %s" % remote)
                continue
            claimed = _claim_one(host, key, ns, remote)
            if not claimed:
                continue
            payload = _pull_payload(host, key, claimed, job_id)
            if payload is None:
                continue
            seen.add(job_id)
            batch.append({
                "job_id": job_id,
                "remote_inflight": claimed,
                "payload": payload,
                "ns": ns,
            })
            _emit({
                "phase": "claimed",
                "id": job_id,
                "root": ns["root"],
                "workers_slot": len(batch),
            })
    return batch


def once(mod, host, key, workers=1, pool=None, pending=None, roots=None):
    """Fill free compute slots and reap finished jobs.

    `pending` is a mutable dict {Future: item} kept across polls so jobs from
    different Kimi lanes / hubs overlap instead of waiting for a full batch.
    """
    LOCAL_Q.mkdir(parents=True, exist_ok=True)
    LOCAL_R.mkdir(parents=True, exist_ok=True)
    if pending is None:
        pending = {}
    if roots is None:
        roots = _thin_roots()
    workers = max(1, int(workers))
    done = 0

    # Reap completed first so slots free for new claims.
    finished = [fut for fut in list(pending.keys()) if fut.done()]
    for fut in finished:
        item = pending.pop(fut)
        job_id = item["job_id"]
        try:
            result = fut.result()
        except Exception as exc:
            result = {
                "id": job_id,
                "ok": False,
                "phase": "eval_exc",
                "host": "mac_research",
                "error": str(exc)[:200],
                "trace": traceback.format_exc()[-240:],
            }
        if _push_result(
            host, key, job_id, item["remote_inflight"], result, item["ns"],
        ):
            done += 1

    free = workers - len(pending)
    if free <= 0:
        return done

    try:
        batch = _gather_batch(host, key, free, roots)
    except Exception as exc:
        _emit({"phase": "list_fail", "error": str(exc)[:200]})
        return done
    if not batch and not pending:
        return done
    if batch:
        _emit({
            "phase": "batch_start",
            "n": len(batch),
            "inflight": len(pending),
            "workers": workers,
            "roots": [r["root"] for r in roots],
            "ids": [b["job_id"] for b in batch],
        })
    for item in batch:
        job_id = item["job_id"]
        payload = item.get("payload") or {}
        # Micro-search: fan out leaf variants across the shared process pool
        # (avoid one serial 32-step job occupying a single worker).
        if (
            str(payload.get("mode") or "") == "micro_search"
            and pool is not None
            and workers > 1
        ):
            try:
                result = _micro_search_parallel(
                    mod, payload, pool=pool, workers=workers,
                )
                ledger = ROOT / "timing_ledger.jsonl"
                try:
                    with ledger.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(
                            {"at": time.time(), **result},
                            ensure_ascii=False, default=str,
                        ) + "\n")
                except Exception:
                    pass
            except Exception as exc:
                result = {
                    "id": job_id,
                    "ok": False,
                    "phase": "eval_exc",
                    "host": "mac_research",
                    "mode": "micro_search",
                    "error": str(exc)[:200],
                    "trace": traceback.format_exc()[-240:],
                }
            if _push_result(
                host, key, job_id, item["remote_inflight"], result, item["ns"],
            ):
                done += 1
            continue
        if pool is not None and workers > 1:
            fut = pool.submit(_pool_eval, payload)
            pending[fut] = item
            _emit({
                "phase": "eval_submit",
                "id": job_id,
                "root": item["ns"]["root"],
                "inflight": len(pending),
                "workers": workers,
            })
            continue
        try:
            result = eval_job(mod, payload)
        except Exception as exc:
            result = {
                "id": job_id,
                "ok": False,
                "phase": "eval_exc",
                "host": "mac_research",
                "error": str(exc)[:200],
                "trace": traceback.format_exc()[-240:],
            }
        if _push_result(
            host, key, job_id, item["remote_inflight"], result, item["ns"],
        ):
            done += 1
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("KDH_THIN_HOST") or DEFAULT_HOST)
    ap.add_argument("--key", default=os.environ.get("KDH_THIN_KEY") or DEFAULT_KEY)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--poll", type=float, default=5.0)
    ap.add_argument("--max-sec", type=float, default=0.0, help="0=forever")
    ap.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("KDH_THIN_WORKERS") or "8"),
        help="parallel isolated_cap processes (default 8)",
    )
    ap.add_argument(
        "--roots",
        default=os.environ.get("KDH_THIN_ROOTS")
        or os.environ.get("KDH_THIN_ROOT")
        or DEFAULT_ROOTS,
        help="comma-separated VPS thin roots (hub-a + hub-b)",
    )
    args = ap.parse_args()
    workers = max(1, min(16, int(args.workers or 1)))
    roots = _thin_roots(args.roots)
    print("VECTOR_ROOT", os.environ.get("VECTOR_ROOT"), flush=True)
    print("host", args.host, flush=True)
    print("workers", workers, flush=True)
    print("roots", [r["root"] for r in roots], flush=True)
    mod = load_kdh_mod()
    t0 = time.time()
    pool = None
    pending = {}
    if workers > 1:
        vector_root = os.environ.get("VECTOR_ROOT") or str(ROOT / ".research_vector")
        pythonpath = os.environ.get("PYTHONPATH") or ("%s:%s" % (ROOT, ROOT / "scripts"))
        pool = ProcessPoolExecutor(
            max_workers=workers,
            initializer=_pool_init,
            initargs=(str(ROOT), vector_root, pythonpath),
        )
        _emit({
            "phase": "pool_start",
            "workers": workers,
            "roots": [r["root"] for r in roots],
        })

    def _stop_pool(signum=None, frame=None):
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)
        if signum is not None:
            raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop_pool)
    signal.signal(signal.SIGINT, _stop_pool)
    try:
        while True:
            try:
                n = once(
                    mod, args.host, args.key,
                    workers=workers, pool=pool, pending=pending, roots=roots,
                )
            except Exception as exc:
                _emit({
                    "phase": "loop_exc",
                    "error": str(exc)[:200],
                    "trace": traceback.format_exc()[-300:],
                })
                time.sleep(max(5.0, float(args.poll)))
                continue
            if args.once:
                while pending:
                    once(
                        mod, args.host, args.key,
                        workers=workers, pool=pool, pending=pending, roots=roots,
                    )
                    time.sleep(0.5)
                return 0 if n >= 0 else 1
            if args.max_sec and (time.time() - t0) >= args.max_sec:
                return 0
            if pending:
                time.sleep(min(1.0, max(0.5, float(args.poll) / 2.0)))
            else:
                time.sleep(max(1.0, float(args.poll)))
    finally:
        if pool is not None:
            pool.shutdown(wait=False)


if __name__ == "__main__":
    sys.exit(main() or 0)
