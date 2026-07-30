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
from . import live_status
from . import metrics
from . import patch_apply
from . import report
from . import review_lexicon as review_lex
from . import review_notify


def _pub(cfg, state, pack, phase="running", result=None, seed_idx=None, seed_max=None, message=None):
    """Best-effort dashboard live status publish (never abort the driver)."""
    try:
        state = dict(state or {})
        if "_t0" not in state and cfg.get("_t0"):
            state["_t0"] = cfg["_t0"]
        return live_status.publish_live_status(
            cfg, state, pack,
            phase=phase, result=result,
            seed_idx=seed_idx, seed_max=seed_max, message=message,
        )
    except Exception as exc:
        print("[auto_driver] live_status publish failed: %s" % exc, flush=True)
        return None


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
    from . import memory_guard

    # Do NOT clear the whole frame cache every seed — that forced 38× reload.
    # Only trim under memory pressure.
    try:
        head = memory_guard.ensure_headroom(min_avail_mb=140, label="pre_seed")
        if not head.get("ok"):
            pipe.trim_frame_cache(2)
        else:
            pipe.trim_frame_cache(6)
    except Exception:
        try:
            pipe.trim_frame_cache(4)
        except Exception:
            pass

    spec = pack["mechanism_spec"]
    dsl = dict(_select_dsl(pack, direction) or {})
    dsl["supported_instruments"] = [symbol]
    dsl["timeframe"] = timeframe
    dsl_mod.validate_strategy(dsl)

    # Local pretest before spending RAM on funnel (fail closed).
    try:
        from dual_engine_workflow_v2.pretest_quality import run_pretest_quality
        pretest = run_pretest_quality(pack, direction=direction)
        if not pretest.get("pass"):
            print(
                "[auto_driver] pretest_quality FAIL quality=%s verdict=%s → refuse funnel"
                % (pretest.get("quality"), pretest.get("verdict")),
                flush=True,
            )
            return {
                "ok": False,
                "reason": "pretest_quality_fail",
                "pretest_quality": pretest,
                "task_id": None,
            }, 0.0
    except Exception as exc:
        print("[auto_driver] pretest_quality exception (fail-closed): %s" % exc, flush=True)
        return {
            "ok": False,
            "reason": "pretest_quality_fail",
            "pretest_quality": {
                "pass": False,
                "quality": "SHIT_TRANSLATION",
                "verdict": "RESET_REQUIRED",
                "reason": "pretest_exception:%s" % exc,
            },
            "task_id": None,
        }, 0.0

    matrix_n = len((spec or {}).get("suitable_symbols") or [])
    budget = memory_guard.matrix_budget()
    print(
        "[auto_driver] pipeline enable_multi_symbol_matrix=%s suitable_symbols=%d "
        "primary=%s ram_mode=%s max_syms=%s avail=%sMB"
        % (
            bool(enable_multi_symbol_matrix), matrix_n, symbol,
            budget.get("mode"), budget.get("max_symbols"), budget.get("avail_mb"),
        ),
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
                "title": (pack.get("meta") or {}).get("title") or spec.get("mechanism_name"),
                "title_zh": (pack.get("meta") or {}).get("title_zh"),
                "source": "auto_driver_wrapper",
                "contract_id": (pack.get("meta") or {}).get("contract_id")
                    or (spec or {}).get("mechanism_family"),
                "pretest_quality_required": True,
                "matrix_generalization": list(
                    (pack.get("meta") or {}).get("matrix_generalization")
                    or (spec or {}).get("suitable_symbols")
                    or []
                ),
            },
            "invariants_contract": pack.get("invariants_contract"),
            "errors": [],
            "call_id": "auto_driver_%s_%s_%d" % (direction, int(time.time()), try_idx),
            "attempts": 0,
        },
        windtalker_tag="%s_ad_try%d" % (tag, try_idx),
        enable_multi_symbol_matrix=bool(enable_multi_symbol_matrix),
    )
    elapsed = round(time.time() - t0, 2)
    try:
        memory_guard.force_release("post_seed")
    except Exception:
        pass
    return result, elapsed


def _l1_seed_retries(pack, cfg, workdir, iteration, state=None):
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
        print("[auto_driver] iter=%d 第二次复核-seed %d/%d" % (iteration, seed, max_seeds), flush=True)
        if state is not None:
            _pub(cfg, state, pack, phase="seed", seed_idx=seed, seed_max=max_seeds,
                 message="第二次复核 seed %d/%d" % (seed, max_seeds))
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
        if state is not None:
            _pub(cfg, state, pack, phase="l1", result=result,
                 seed_idx=seed, seed_max=max_seeds)
        reason = str((result or {}).get("reason") or "")
        if (result or {}).get("ok"):
            return result, "success"
        # L0 density reject is deterministic for a pack — no seed retry burn
        if reason == "funnel_l0_fail":
            return result, "non_l1"
        if reason != "funnel_l1_fail":
            return result, "non_l1"
        # else continue seeding
    return last, "l1_exhausted"


def run_driver(cfg):
    """Main autonomous loop. cfg is a dict (see auto_driver_config.example.json)."""
    t_start = time.time()
    root = _bootstrap_root(cfg.get("vector_root"))

    # Memory guard first — protect web on ≤1GB hosts
    try:
        from . import memory_guard
        lim = memory_guard.install_soft_rlimit()
        budget = memory_guard.matrix_budget()
        print(
            "[auto_driver] memory_guard soft_rss≈%sMB install=%s ram_mode=%s avail=%sMB"
            % (
                lim.get("soft_rss_mb"), lim.get("ok"),
                budget.get("mode"), budget.get("avail_mb"),
            ),
            flush=True,
        )
    except Exception as exc:
        print("[auto_driver] memory_guard init failed: %s" % exc, flush=True)

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
        "ai_optimize_count": 0,
        "ai_optimize_cap_hit": False,
        "wx_notify": None,
        "archive_record": None,
        "human_confirm": None,
        "final_status": None,
        "stop_code": None,
        "stop_detail": {},
        "limit_analysis": None,
        "review_suggestions": None,
        "n_iterations": 0,
    }

    max_iter = int(cfg.get("max_iterations") or 4)  # 1 eval + ≤3 AI rounds by default
    max_ai_optimize = int(cfg.get("max_ai_optimize") or 3)
    kb_bumps = 0
    max_kb_bumps = int(cfg.get("max_kb_family_bumps") or 3)
    cfg = dict(cfg)
    cfg["workdir"] = str(workdir)
    cfg["max_ai_optimize"] = max_ai_optimize
    cfg["_t0"] = t_start
    state["_t0"] = t_start

    print(
        "[auto_driver] start workdir=%s pack=%s dry_run=%s max_ai_optimize=%d"
        % (workdir, pack_path, dry_run, max_ai_optimize),
        flush=True,
    )
    _pub(cfg, state, pack, phase="running", message="Auto-Driver 启动 · 三复核流水线")

    for iteration in range(1, max_iter + 1):
        state["n_iterations"] = iteration
        state["elapsed_sec"] = round(time.time() - t_start, 2)
        iter_dir = workdir / ("iter_%02d" % iteration)
        iter_dir.mkdir(parents=True, exist_ok=True)

        # snapshot before mutation / run
        snap = patch_apply.snapshot_pack(pack)
        _dump_json(iter_dir / "pack.before.json", snap)

        print("[auto_driver] === iteration %d/%d family=%s ===" % (
            iteration, max_iter,
            ((pack.get("mechanism_spec") or {}).get("mechanism_family")),
        ), flush=True)
        _pub(cfg, state, pack, phase="seed",
             message="Round %d/%d 开始" % (iteration, max_iter))

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
            result, seed_mode = _l1_seed_retries(pack, cfg, iter_dir, iteration, state=state)
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
                _pub(cfg, state, pack, phase="success", result=result)
                break

        ctx = metrics.build_failure_context(
            result, pack, iteration,
            cfg["symbol"], cfg["timeframe"], cfg["direction"],
            optimize_goals=cfg.get("optimize_goals"),
            notes=cfg.get("notes"),
        )
        _dump_json(iter_dir / "failure_context.json", ctx)
        _pub(cfg, state, pack, phase=str((result or {}).get("reason") or "l1"), result=result)

        reason = str((result or {}).get("reason") or "")
        print("[auto_driver] reason=%s score=%.4f gap=%.4f" % (
            reason, ctx.get("composite_score") or 0, metrics.total_gap(ctx.get("metric_gaps")),
        ), flush=True)

        # Clean pack but L0 too rare: do NOT loosen entry to decorate density.
        if reason == "funnel_l0_fail":
            pq = (result or {}).get("pretest_quality") or ctx.get("pretest_quality") or {}
            if str(pq.get("quality") or "") == "ok" or pq.get("pass"):
                state["ai_limit_reached"] = True
                state["stop_code"] = "L0_SPARSE_CLEAN_PACK"
                state["ai_limit_reason"] = (
                    "第一次复核：清洁包密度过稀；拒绝放宽入场雕花"
                )
                state["iterations"].append(_iter_record(
                    iteration, result, ctx, elapsed,
                    ai_decision="LIMIT_REACHED", applied=[],
                    ai_rationale=state["ai_limit_reason"],
                    limit_reason=state["ai_limit_reason"],
                ))
                _dump_json(iter_dir / "pack.after.json", pack)
                _pub(cfg, state, pack, phase="l0_sparse_limit", result=result,
                     message=state["ai_limit_reason"])
                print("[auto_driver] 第一次复核密度过稀(清洁包) → LIMIT (no decorate)", flush=True)
                break

        # pretest fail = SHIT_TRANSLATION → RESET only, never call additive AI
        if reason == "pretest_quality_fail":
            pq = (result or {}).get("pretest_quality") or ctx.get("pretest_quality") or {}
            state["ai_limit_reached"] = True
            state["stop_code"] = "RESET_REQUIRED"
            state["ai_limit_reason"] = (
                pq.get("message_zh")
                or pq.get("reason")
                or "pretest_quality_fail → RESET, no shit-decorate"
            )
            state["iterations"].append(_iter_record(
                iteration, result, ctx, elapsed,
                ai_decision="RESET", applied=[],
                ai_rationale=state["ai_limit_reason"],
                limit_reason=state["ai_limit_reason"],
            ))
            _dump_json(iter_dir / "pack.after.json", pack)
            _pub(cfg, state, pack, phase="reset_required", result=result,
                 message=state["ai_limit_reason"])
            print("[auto_driver] pretest SHIT_TRANSLATION → RESET stop (no AI decorate)", flush=True)
            break

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

        # --- AI optimize (hard cap ≤ max_ai_optimize, default 3) ---
        if int(state.get("ai_optimize_count") or 0) >= max_ai_optimize:
            state["ai_optimize_cap_hit"] = True
            state["stop_code"] = "MAX_AI_OPTIMIZE"
            state["stop_detail"] = {
                "message": "ai_optimize_rounds_exhausted",
                "ai_optimize_count": state.get("ai_optimize_count"),
                "max_ai_optimize": max_ai_optimize,
                "last_reason": reason,
            }
            state["iterations"].append(_iter_record(
                iteration, result, ctx, elapsed,
                ai_decision="MAX_AI_OPTIMIZE", applied=[],
                ai_rationale="AI optimize cap reached; refuse further decorate",
                limit_reason="max_ai_optimize=%d" % max_ai_optimize,
            ))
            _dump_json(iter_dir / "pack.after.json", pack)
            _pub(cfg, state, pack, phase="max_ai_optimize", result=result,
                 message="AI 优化已达上限 %d 轮" % max_ai_optimize)
            print("[auto_driver] MAX_AI_OPTIMIZE hit (%d) → stop" % max_ai_optimize, flush=True)
            break

        history = ai_optimize.history_tail_from_iterations(state["iterations"], n=3)
        state["elapsed_sec"] = round(time.time() - t_start, 2)
        _pub(cfg, state, pack, phase="calling_ai", result=result,
             message="三方 AI 出补丁中（%d/%d）" % (
                 int(state.get("ai_optimize_count") or 0) + 1, max_ai_optimize))
        if dry_run and cfg.get("dry_run_skip_ai"):
            merged = {
                "decision": "LIMIT_REACHED",
                "rationale": "dry_run_skip_ai",
                "limit_reason": "dry_run mode skipped AI calls",
                "patches": [],
                "provider_rows": [],
            }
        else:
            print("[auto_driver] calling AI providers: %s (round %d/%d)" % (
                providers, int(state.get("ai_optimize_count") or 0) + 1, max_ai_optimize), flush=True)
            rows = ai_optimize.propose_from_all(
                providers, ctx, history_tail=history,
                max_tokens=int(cfg.get("ai_max_tokens") or 2800),
            )
            _dump_json(iter_dir / "ai_provider_rows.json", rows)
            merged = ai_optimize.merge_proposals(rows, prefer_provider=prefer)
            state["ai_optimize_count"] = int(state.get("ai_optimize_count") or 0) + 1
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
            _pub(cfg, state, pack, phase="ai_limit_reached", result=result,
                 message=state["ai_limit_reason"])
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
            _pub(cfg, state, pack, phase="stopped", result=result,
                 message=state["ai_abort_reason"])
            break

        if ai_decision == "RESET":
            # Semantic drift / shit translation — refuse additive decorate.
            state["ai_limit_reached"] = True
            state["ai_limit_reason"] = (
                merged.get("rationale")
                or "RESET_REQUIRED: translation drifted; retranslate from contract"
            )
            state["stop_code"] = "RESET_REQUIRED"
            state["iterations"].append(_iter_record(
                iteration, result, ctx, elapsed,
                ai_decision="RESET", applied=[],
                ai_rationale=state["ai_limit_reason"],
                limit_reason=state["ai_limit_reason"],
            ))
            _dump_json(iter_dir / "pack.after.json", pack)
            _pub(cfg, state, pack, phase="reset_required", result=result,
                 message=state["ai_limit_reason"])
            print("[auto_driver] RESET — refuse shit-decorate; stop loop", flush=True)
            break

        # PRUNE (legacy PATCH alias) — subtractive / contract-restore only
        if ai_decision in ("PRUNE", "PATCH"):
            _pub(cfg, state, pack, phase="patch", result=result,
                 message="剪枝/契约恢复并 DSL 校验")
            # Gate2-after-L1: drop entry mutations; then clamp DSL hard bounds.
            patches = patch_apply.filter_patches_for_reason(
                merged.get("patches") or [], reason,
            )
            patches = patch_apply.sanitize_patches(patches)
            applied = []
            apply_errors = []
            if not patches:
                print("[auto_driver] no usable prune patches after filter/sanitize; skip apply", flush=True)
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
                    # DSL validate + pretest; rollback if invalid / shit
                    try:
                        import auto_trade_strategy_dsl as dsl_mod
                        from dual_engine_workflow_v2.pretest_quality import run_pretest_quality
                        dsl = _select_dsl(new_pack, cfg["direction"])
                        dsl = dict(dsl or {})
                        dsl["supported_instruments"] = [cfg["symbol"]]
                        dsl["timeframe"] = cfg["timeframe"]
                        dsl_mod.validate_strategy(dsl)
                        pretest2 = run_pretest_quality(new_pack, direction=cfg["direction"])
                        if not pretest2.get("pass"):
                            raise RuntimeError(
                                "pretest_after_prune_failed:%s" % pretest2.get("reason")
                            )
                        pack = new_pack
                    except Exception as exc:
                        print("[auto_driver] validate/pretest failed after prune: %s; rollback" % exc, flush=True)
                        pack = patch_apply.restore_pack(snap)
                        apply_errors.append({"error": "dsl_or_pretest_failed", "detail": str(exc)})
                        applied = []
        else:
            print("[auto_driver] unexpected ai_decision=%s" % ai_decision, flush=True)

        # After Gate2 viability-style fails, proactively bump family for next submit
        # to reduce immediate kb_blocked on the next iteration.
        if reason in ("repair_exhausted_or_drift", "gate2_3_fail") and cfg.get("auto_bump_family_after_gate2", True):
            pack, new_fam = patch_apply.bump_family_for_kb(pack, iteration + 1)
            print("[auto_driver] 第三次复核后家族重命名 → %s" % new_fam, flush=True)
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
        if state.get("ai_optimize_cap_hit") and not state.get("stop_code"):
            state["stop_code"] = "MAX_AI_OPTIMIZE"
            state["stop_detail"] = detail or {
                "message": "ai_optimize_rounds_exhausted",
                "ai_optimize_count": state.get("ai_optimize_count"),
            }
        elif not state.get("stop_code"):
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

    # ── WxPusher terminal hooks (same channel as human confirm) ──────────
    # SUCCESS: reuse existing strategy_pending_confirm path (pipeline may
    # already have enqueued). FAIL: NEW strategy_review_failed + archive.
    last_reason = None
    last_diag_cause = None
    iters = state.get("iterations") or []
    if iters:
        last_reason = iters[-1].get("pipeline_reason")
        diag = iters[-1].get("diagnostic") or {}
        if isinstance(diag, dict):
            last_diag_cause = (
                (diag.get("main_cause") or {}).get("title_zh")
                or diag.get("main_cause_line")
                or diag.get("fatal_line")
            )
    review_n = review_lex.review_n_from_reason(last_reason) or review_lex.review_n_from_reason(
        state.get("stop_code")
    )

    if state.get("success") and not dry_run and not cfg.get("skip_wx_notify"):
        # Prefer the last successful pipeline result if available on disk
        success_result = {}
        try:
            for seed_file in sorted(workdir.glob("iter_*_seed_*_result.json"), reverse=True):
                blob = _load_json(seed_file)
                r = (blob or {}).get("result") or {}
                if r.get("ok"):
                    success_result = r
                    break
        except Exception:
            success_result = {}
        hc = review_notify.ensure_human_confirm_on_success(
            result=success_result, pack=final_pack, cfg=cfg,
        )
        state["human_confirm"] = hc
        print("[auto_driver] human_confirm channel=%s ok=%s key=%s" % (
            hc.get("channel"), hc.get("ok"), hc.get("key")), flush=True)
    elif (not state.get("success")) and not dry_run and not cfg.get("skip_wx_notify"):
        skip_fail_wx = bool(cfg.get("skip_failure_wx"))
        arch = review_notify.archive_failed_pack(
            final_pack,
            reason=last_reason,
            review_n=review_n,
            stop_code=state.get("stop_code"),
            workdir=workdir,
            vector_root=root,
            extra={
                "final_status": state.get("final_status"),
                "ai_optimize_count": state.get("ai_optimize_count"),
            },
        )
        state["archive_record"] = arch
        if not skip_fail_wx:
            wx = review_notify.notify_strategy_failure(
                pack=final_pack,
                cfg=cfg,
                reason=last_reason,
                stop_code=state.get("stop_code"),
                review_n=review_n,
                core_cause=last_diag_cause or last_reason or state.get("ai_limit_reason"),
                ai_optimize_used=state.get("ai_optimize_count"),
                archive_record=arch,
                dry_run=False,
            )
            state["wx_notify"] = wx
            print("[auto_driver] failure_wx kind=strategy_review_failed sent=%s review=%s" % (
                wx.get("sent"), review_lex.review_label(review_n)), flush=True)
        else:
            print("[auto_driver] failure archived at %s (wx skipped)" % arch.get("pack_path"), flush=True)

    _dump_json(workdir / "driver_state.json", state)

    end_phase = "success" if state.get("success") else str(state.get("stop_code") or "limit_reached_failed").lower()
    _pub(cfg, state, final_pack, phase=end_phase,
         message="终态 %s · %s · 报告 %s" % (
             state["final_status"], state["stop_code"], report_path.name))

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
        "human_confirm": state.get("human_confirm"),
        "archive_record": state.get("archive_record"),
        "wx_notify": state.get("wx_notify"),
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
        "l0": ctx.get("l0") or {},
        "gate2": {
            "payoff_ratio": g2.get("payoff_ratio"),
            "calmar": g2.get("calmar"),
            "worst5_loss_share": g2.get("worst5_loss_share"),
            "expectancy_factor": g2.get("expectancy_factor"),
            "sample_size": g2.get("sample_size"),
            "pass": g2.get("pass"),
            "failed_checks": g2.get("failed_checks"),
            "verdict_tags": g2.get("verdict_tags"),
        },
        "diagnostic": ctx.get("diagnostic"),
        "ai_decision": ai_decision,
        "ai_rationale": ai_rationale,
        "limit_reason": limit_reason,
        "applied_patches": applied or [],
        "apply_errors": apply_errors or [],
        "chosen_provider": chosen_provider,
    }
