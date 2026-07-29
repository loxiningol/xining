# -*- coding: utf-8 -*-
"""Theoretical limit / convergence detectors for the auto-driver loop."""
from __future__ import print_function


def detect_limits(state, cfg):
    """Return (should_stop, code, detail).

    Codes:
      SUCCESS
      MAX_ITERATIONS
      CONVERGED_NO_IMPROVEMENT
      AI_LIMIT_REACHED
      AI_ABORT
      KB_BLOCKED_EXHAUSTED
      NO_PROGRESS_REPEATED_REASON
    """
    iterations = state.get("iterations") or []
    max_iter = int(cfg.get("max_iterations") or 10)
    eps = float(cfg.get("convergence_eps") or 0.02)
    patience = int(cfg.get("convergence_patience") or 3)
    stagnant_reason_n = int(cfg.get("stagnant_reason_patience") or 4)

    if state.get("success"):
        return True, "SUCCESS", {"message": "strategy_passed_pipeline"}

    if len(iterations) >= max_iter:
        return True, "MAX_ITERATIONS", {
            "message": "reached max_iterations=%d" % max_iter,
            "n": len(iterations),
        }

    # AI explicit limit
    if state.get("ai_limit_reached"):
        return True, "AI_LIMIT_REACHED", {
            "message": state.get("ai_limit_reason") or "providers_declared_limit",
        }

    if state.get("ai_abort"):
        return True, "AI_ABORT", {
            "message": state.get("ai_abort_reason") or "ai_unavailable_or_refused",
        }

    if state.get("kb_blocked_exhausted"):
        return True, "KB_BLOCKED_EXHAUSTED", {
            "message": "kb_blocked after family bumps exhausted",
        }

    # score convergence
    scores = [it.get("composite_score") for it in iterations if it.get("composite_score") is not None]
    if len(scores) >= patience + 1:
        recent = scores[-(patience + 1):]
        deltas = [recent[i + 1] - recent[i] for i in range(len(recent) - 1)]
        if all(abs(d) < eps for d in deltas) or all(d <= eps for d in deltas):
            # also require gaps not collapsing to zero
            gaps = [it.get("total_gap") for it in iterations[-patience:] if it.get("total_gap") is not None]
            if gaps and min(gaps) > float(cfg.get("gap_success_eps") or 1e-6):
                return True, "CONVERGED_NO_IMPROVEMENT", {
                    "message": "composite_score stagnated for %d rounds (eps=%.4f)" % (patience, eps),
                    "recent_scores": recent,
                    "deltas": deltas,
                    "recent_gaps": gaps,
                }

    # repeated identical pipeline reason with no score lift
    if len(iterations) >= stagnant_reason_n:
        tail = iterations[-stagnant_reason_n:]
        reasons = [str(it.get("pipeline_reason") or "") for it in tail]
        if len(set(reasons)) == 1 and reasons[0] not in ("", "None"):
            s0 = tail[0].get("composite_score")
            s1 = tail[-1].get("composite_score")
            try:
                lift = float(s1) - float(s0)
            except Exception:
                lift = 0.0
            if lift < eps:
                return True, "NO_PROGRESS_REPEATED_REASON", {
                    "message": "same pipeline_reason=%s for %d rounds without score lift" % (
                        reasons[0], stagnant_reason_n),
                    "lift": lift,
                }

    return False, None, {}
