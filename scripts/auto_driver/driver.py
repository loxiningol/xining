# -*- coding: utf-8 -*-
"""Autonomous STEP A Gate-retry driver loop (wrapper; no core pipeline edits)."""
from __future__ import print_function

import json
import os
import shutil
import time
import traceback
from pathlib import Path

from . import ai_optimize
from . import limits
from . import metrics
from . import patch_apply
from . import report


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _dump_json(path, obj):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _bootstrap_root(vector_root):
    root = Path(vector_root or os.environ.get("VECTOR_ROOT") or "/root")
    os.environ["VECTOR_ROOT"] = str(root)
    import sys
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.chdir(str(root))
    return root


def _select_dsl(pack, direction):
    if str(direction).lower() == "short":
        return pack.get("dsl_short") or pack.get("dsl")
    return pack.get("dsl_long") or pack.get("dsl")


def run_once_step_a(pack, symbol, timeframe, direction, tag, try_idx=1,
                    enable_multi_symbol_matrix=True):
    """Call existing STEP A pipeline with prebuilt pack. Never mounts / confirms."""
    import auto_trade_strategy_dsl as dsl_mod
    import auto_trade_human_confirm_pipeline as pipe
    from dual_engine_workflow_v2.pipeline_step_a import run_creation_pipeline_step_a

    pipe._FRAME_CACHE.clear()
    spec = pack["mechanism_spec"]
    dsl = dict(_select_dsl(pack, direction) or {})
    dsl["supported_instruments"] = [symbol]
    dsl["timeframe"] = timeframe
    dsl_mod.validate_strategy(dsl)

    matrix_n = len((spec or {}).get("suitable_symbols") or [])
    print(
        "[auto_driver] pipeline enable_multi_symbol_matrix=%s suitable_symbols=%d primary=%s"
        % (bool(enable_multi_symbol_matrix), matrix_n, symbol),
        flush=True,
    )

    t0 = time.time()
    result = run_creation_pipeline_step_a(
        symbol=symbol,
        timeframe=timeframe,
        exploration_mode="A",
        allow_horizontal_expand=False,
        prebuilt_spec_pack={
            "ok": True,
            "mechanism_spec": spec,
            "dsl": dsl,
            "dsl_long": pack.get("dsl_long"),
            "dsl_short": pack.get("dsl_short"),
            "meta": {
                "symbol": symbol,
                "timeframe": timeframe,
                "direction": direction,
                "title": spec.get("mechanism_name"),
                "source": "auto_driver_wrapper",
                "matrix_generalization": list(
                    (pack.get("meta") or {}).get("matrix_generalization")
                    or (spec or {}).get("suitable_symbols")
                    or []
                ),
            },
            "errors": [],
            "call_id": "auto_driver_%s_%s_%d" % (direction, int(time.time()), try_idx),
            "attempts": 0,
        },
        windtalker_tag="%s_ad_try%d" % (tag, try_idx),
        enable_multi_symbol_matrix=bool(enable_multi_symbol_matrix),
    )
    elapsed = round(time.time() - t0, 2)
    return result, elapsed


def _l1_seed_retries(pack, cfg, workdir, iteration):
    """Mirror submit_* L1 seed retries without AI (cheap)."""
    max_seeds = int(cfg.get("l1_seed_retries") or 6)
    symbol = cfg["symbol"]
    timeframe = cfg["timeframe"]
    direction = cfg["direction"]
    tag = cfg.get("tag") or "auto_driver"
    # Always enable matrix for auto-driver eval (config may only turn it off explicitly).
    enable_matrix = cfg.get("enable_multi_symbol_matrix", True)
    if enable_matrix is None:
        enable_matrix = True
    last = None
    for seed in range(1, max_seeds + 1):
        print("[auto_driver] iter=%d L1-seed %d/%d" % (iteration, seed, max_seeds), flush=True)
        try:
            result, elapsed = run_once_step_a(
                pack, symbol, timeframe, direction, tag, try_idx=seed,
                enable_multi_symbol_matrix=bool(enable_matrix),
            )
        except Exception as exc:
            result = {
                "ok": False,
                "reason": "exception",
                "error": str(exc),
                "trace": traceback.format_exc()[-800:],
            }
            elapsed = 0.0
        _dump_json(workdir / ("iter_%02d_seed_%02d_result.json" % (iteration, seed)), {
            "elapsed": elapsed,
            "enable_multi_symbol_matrix": bool(enable_matrix),
            "result": result,
        })
        last = result
        reason = str((result or {}).get("reason") or "")
        if (result or {}).get("ok"):
            return result, "success"
        if reason != "funnel_l1_fail":
            return result, "non_l1"
        # else continue seeding
    return last, "l1_exhausted"


def run_driver(cfg):
    """Main autonomous loop. cfg is a dict (see auto_driver_config.example.json)."""
    t_start = time.time()
    root = _bootstrap_root(cfg.get("vector_root"))

    pack_path = Path(cfg["pack_path"])
    if not pack_path.is_absolute():
        pack_path = root / pack_path
    if not pack_path.exists():
        # also try relative to CWD / workspace
        alt = Path(cfg["pack_path"])
        if alt.exists():
            pack_path = alt
        else:
            raise FileNotFoundError("pack not found: %s" % cfg["pack_path"])

    workdir = Path(cfg.get("workdir") or (root / "auto_trade" / "dual_engine" / "workflow_v2" / "auto_driver_runs" / time.strftime("%Y%m%d_%H%M%S")))
    workdir.mkdir(parents=True, exist_ok=True)

    initial_pack = _load_json(pack_path)
    pack = patch_apply.deep_copy_pack(initial_pack)
    _dump_json(workdir / "pack_initial.json", initial_pack)
    _dump_json(workdir / "config.used.json", cfg)

    providers = list(cfg.get("ai_providers") or ["deepseek", "qwen", "glm"])
    prefer = cfg.get("prefer_provider") or "glm"
    dry_run = bool(cfg.get("dry_run"))
    skip_pipeline = bool(cfg.get("skip_pipeline"))  # for offline AI/report smoke

    state = {
        "iterations": [],
        "success": False,
        "workdir": str(workdir),
        "ai_limit_reached": False,
        "ai_limit_reason": None,
        "ai_abort": False,
        "ai_abort_reason": None,
        "kb_blocked_exhausted": False,
        "final_status": None,
        "stop_code": None,
        "stop_detail": {},
        "limit_analysis": None,
        "review_suggestions": None,
        "n_iterations": 0,
    }

    max_iter = int(cfg.get("max_iterations") or 10)
    kb_bumps = 0
    max_kb_bumps = int(cfg.get("max_kb_family_bumps") or 3)

    print("[auto_driver] start workdir=%s pack=%s dry_run=%s" % (workdir, pack_path, dry_run), flush=True)

    for iteration in range(1, max_iter + 1):
        state["n_iterations"] = iteration
        iter_dir = workdir / ("iter_%02d" % iteration)
        iter_dir.mkdir(parents=True, exist_ok=True)

        # snapshot before mutation / run
        snap = patch_apply.snapshot_pack(pack)
        _dump_json(iter_dir / "pack.before.json", snap)

        print("[auto_driver] === iteration %d/%d family=%s ===" % (
            iteration, max_iter,
            ((pack.get("mechanism_spec") or {}).get("mechanism_family")),
        ), flush=True)

        if skip_pipeline or dry_run:
            # Synthetic failure context for dry-run / AI-only smoke
            result = cfg.get("dry_run_fake_result") or {
                "ok": False,
                "reason": "funnel_l1_fail",
                "task_id": "dry_run_fake",
                "phase3_funnel": {
                    "l1_micro_screen": {
                        "pass": False,
                        "reject_reasons": ["sample_filled_entries_lt_5"],
                        "metrics": {"filled_entries": 2, "payoff_ratio": 0.8},
                    }
                },
                "gate_results": {"gates": []},
            }
            elapsed = 0.0
        else:
            result, seed_mode = _l1_seed_retries(pack, cfg, iter_dir, iteration)
            elapsed = 0.0  # detailed in seed files
            if seed_mode == "success":
                state["success"] = True
                ctx = metrics.build_failure_context(
                    result, pack, iteration,
                    cfg["symbol"], cfg["timeframe"], cfg["direction"],
                    optimize_goals=cfg.get("optimize_goals"),
                    notes=cfg.get("notes"),
                )
                state["iterations"].append(_iter_record(
                    iteration, result, ctx, elapsed,
                    ai_decision="N/A", applied=[], ai_rationale="passed_without_further_ai",
                ))
                _dump_json(iter_dir / "pack.after.json", pack)
                break

        ctx = metrics.build_failure_context(
            result, pack, iteration,
            cfg["symbol"], cfg["timeframe"], cfg["direction"],
            optimize_goals=cfg.get("optimize_goals"),
            notes=cfg.get("notes"),
        )
        _dump_json(iter_dir / "failure_context.json", ctx)

        reason = str((result or {}).get("reason") or "")
        print("[auto_driver] reason=%s score=%.4f gap=%.4f" % (
            reason, ctx.get("composite_score") or 0, metrics.total_gap(ctx.get("metric_gaps")),
        ), flush=True)

        # kb_blocked → bump family lineage then continue (still may AI-patch)
        if reason == "kb_blocked":
            kb_bumps += 1
            if kb_bumps > max_kb_bumps:
                state["kb_blocked_exhausted"] = True
                state["iterations"].append(_iter_record(
                    iteration, result, ctx, elapsed,
                    ai_decision="KB_BLOCKED", applied=[],
                    ai_rationale="family_blocked_bumps_exhausted",
                ))
                break
            pack, new_fam = patch_apply.bump_family_for_kb(pack, iteration)
            print("[auto_driver] kb_blocked → rename family to %s" % new_fam, flush=True)
            _dump_json(iter_dir / "pack.family_bumped.json", pack)
            # fall through to AI optimize on bumped pack

        # If already ok (non dry)
        if (result or {}).get("ok"):
            state["success"] = True
            state["iterations"].append(_iter_record(
                iteration, result, ctx, elapsed,
                ai_decision="N/A", applied=[], ai_rationale="ok",
            ))
            break

        # --- AI optimize ---
        history = ai_optimize.history_tail_from_iterations(state["iterations"], n=3)
        if dry_run and cfg.get("dry_run_skip_ai"):
            merged = {
                "decision": "LIMIT_REACHED",
                "rationale": "dry_run_skip_ai",
                "limit_reason": "dry_run mode skipped AI calls",
                "patches": [],
                "provider_rows": [],
            }
        else:
            print("[auto_driver] calling AI providers: %s" % providers, flush=True)
            rows = ai_optimize.propose_from_all(
                providers, ctx, history_tail=history,
                max_tokens=int(cfg.get("ai_max_tokens") or 2800),
            )
            _dump_json(iter_dir / "ai_provider_rows.json", rows)
            merged = ai_optimize.merge_proposals(rows, prefer_provider=prefer)
        _dump_json(iter_dir / "ai_merged.json", merged)

        ai_decision = str(merged.get("decision") or "").upper()
        applied = []
        apply_errors = []

        if ai_decision == "LIMIT_REACHED":
            state["ai_limit_reached"] = True
            state["ai_limit_reason"] = merged.get("limit_reason") or merged.get("rationale")
            state["iterations"].append(_iter_record(
                iteration, result, ctx, elapsed,
                ai_decision=ai_decision, applied=[],
                ai_rationale=merged.get("rationale"),
                limit_reason=state["ai_limit_reason"],
            ))
            _dump_json(iter_dir / "pack.after.json", pack)
            break

        if ai_decision == "ABORT":
            state["ai_abort"] = True
            state["ai_abort_reason"] = merged.get("rationale") or str(merged.get("errors"))
            state["iterations"].append(_iter_record(
                iteration, result, ctx, elapsed,
                ai_decision=ai_decision, applied=[],
                ai_rationale=state["ai_abort_reason"],
            ))
            _dump_json(iter_dir / "pack.after.json", pack)
            break

        # PATCH
        if ai_decision == "PATCH":
            # Gate2-after-L1: drop entry mutations; then clamp DSL hard bounds.
            patches = patch_apply.filter_patches_for_reason(
                merged.get("patches") or [], reason,
            )
            patches = patch_apply.sanitize_patches(patches)
            applied = []
            apply_errors = []
            if not patches:
                print("[auto_driver] no usable patches after Gate2 entry-filter/sanitize; skip apply", flush=True)
                apply_errors = [{"error": "no_usable_patches_after_filter"}]
                _dump_json(iter_dir / "patches.applied.json", {
                    "applied": [], "errors": apply_errors, "filtered": True,
                })
            else:
                new_pack, applied, apply_errors = patch_apply.apply_patches(
                    pack, patches, direction=cfg["direction"],
                )
                _dump_json(iter_dir / "patches.applied.json", {"applied": applied, "errors": apply_errors})
                if apply_errors and not applied:
                    print("[auto_driver] all patches failed; rollback", flush=True)
                    pack = patch_apply.restore_pack(snap)
                else:
                    # DSL validate; rollback if invalid
                    try:
                        import auto_trade_strategy_dsl as dsl_mod
                        dsl = _select_dsl(new_pack, cfg["direction"])
                        dsl = dict(dsl or {})
                        dsl["supported_instruments"] = [cfg["symbol"]]
                        dsl["timeframe"] = cfg["timeframe"]
                        dsl_mod.validate_strategy(dsl)
                        pack = new_pack
                    except Exception as exc:
                        print("[auto_driver] DSL validate failed after patch: %s; rollback" % exc, flush=True)
                        pack = patch_apply.restore_pack(snap)
                        apply_errors.append({"error": "dsl_validate_failed", "detail": str(exc)})
                        applied = []
        else:
            print("[auto_driver] unexpected ai_decision=%s" % ai_decision, flush=True)

        # After Gate2 viability-style fails, proactively bump family for next submit
        # to reduce immediate kb_blocked on the next iteration.
        if reason in ("repair_exhausted_or_drift", "gate2_3_fail") and cfg.get("auto_bump_family_after_gate2", True):
            pack, new_fam = patch_apply.bump_family_for_kb(pack, iteration + 1)
            print("[auto_driver] post-gate2 family bump → %s" % new_fam, flush=True)
            applied.append({"op": "rename_family", "to": new_fam, "auto": True})

        _dump_json(iter_dir / "pack.after.json", pack)
        state["iterations"].append(_iter_record(
            iteration, result, ctx, elapsed,
            ai_decision=ai_decision, applied=applied,
            ai_rationale=merged.get("rationale"),
            limit_reason=merged.get("limit_reason"),
            apply_errors=apply_errors,
            chosen_provider=merged.get("chosen_provider"),
        ))

        # mid-loop limit check (after recording)
        stop, code, detail = limits.detect_limits(state, cfg)
        if stop:
            state["stop_code"] = code
            state["stop_detail"] = detail
            break

        if dry_run:
            # one AI cycle enough for dry-run unless configured otherwise
            if not cfg.get("dry_run_multi_iter"):
                state["ai_limit_reached"] = True
                state["ai_limit_reason"] = state.get("ai_limit_reason") or "dry_run_single_iter_complete"
                break

    # final stop classification
    if state.get("success"):
        state["final_status"] = "SUCCESS"
        state["stop_code"] = state.get("stop_code") or "SUCCESS"
    else:
        stop, code, detail = limits.detect_limits(state, cfg)
        if not state.get("stop_code"):
            state["stop_code"] = code or "MAX_ITERATIONS"
            state["stop_detail"] = detail or {"message": "loop_ended"}
        state["final_status"] = "LIMIT_REACHED_FAILED"

    state["elapsed_sec"] = round(time.time() - t_start, 2)
    state["n_iterations"] = len(state.get("iterations") or [])

    final_pack = pack
    _dump_json(workdir / "pack_final.json", final_pack)
    _dump_json(workdir / "driver_state.json", state)

    report_path = workdir / (cfg.get("report_name") or "delivery_report.md")
    report.write_delivery_report(str(report_path), state, cfg, initial_pack, final_pack)
    # also copy to CWD-friendly path if requested
    if cfg.get("report_copy_to"):
        dest = Path(cfg["report_copy_to"])
        if not dest.is_absolute():
            dest = root / dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(report_path), str(dest))
        print("[auto_driver] report copied to %s" % dest, flush=True)

    print("[auto_driver] DONE status=%s stop=%s report=%s" % (
        state["final_status"], state["stop_code"], report_path), flush=True)
    return {
        "ok": bool(state.get("success")),
        "final_status": state["final_status"],
        "stop_code": state["stop_code"],
        "workdir": str(workdir),
        "report_path": str(report_path),
        "state": state,
        "final_pack": final_pack,
    }


def _iter_record(iteration, result, ctx, elapsed, ai_decision=None, applied=None,
                 ai_rationale=None, limit_reason=None, apply_errors=None, chosen_provider=None):
    g2 = ctx.get("gate2_fitness") or {}
    l1 = ctx.get("l1") or {}
    return {
        "iteration": iteration,
        "task_id": (result or {}).get("task_id"),
        "pipeline_reason": (result or {}).get("reason") or (result or {}).get("stage"),
        "elapsed_sec": elapsed,
        "composite_score": ctx.get("composite_score"),
        "total_gap": metrics.total_gap(ctx.get("metric_gaps")),
        "metric_gaps": ctx.get("metric_gaps"),
        "failed_checks": g2.get("failed_checks"),
        "l1_reject": l1.get("reject_reasons"),
        "l1": l1,
        "gate2": {
            "payoff_ratio": g2.get("payoff_ratio"),
            "calmar": g2.get("calmar"),
            "worst5_loss_share": g2.get("worst5_loss_share"),
            "expectancy_factor": g2.get("expectancy_factor"),
            "sample_size": g2.get("sample_size"),
            "pass": g2.get("pass"),
        },
        "ai_decision": ai_decision,
        "ai_rationale": ai_rationale,
        "limit_reason": limit_reason,
        "applied_patches": applied or [],
        "apply_errors": apply_errors or [],
        "chosen_provider": chosen_provider,
    }
