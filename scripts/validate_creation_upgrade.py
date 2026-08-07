#!/usr/bin/env python3
"""Validate creation upgrade: meta design seeds + param rescue + expanded budget."""
from __future__ import print_function

import json
import os
import sys
import time
from pathlib import Path

root = Path(os.environ.get("VECTOR_ROOT") or Path(__file__).resolve().parents[1])
os.chdir(str(root))
sys.path.insert(0, str(root))

BRIEF = "均线金叉做多"
SYMBOL = "ETH-USDT-SWAP"
TF = "5m"
DIRECTION = "long"

os.environ.setdefault("QIYU_SKIP_ALPHA_DISCOVERY", "1")
os.environ.setdefault("QIYU_CREATION_MAX_BARS", "52000")
os.environ.setdefault("QIYU_CREATION_WITH_LLM", "1")
os.environ.setdefault("QIYU_PARAM_MAX_EVALS", "48")
os.environ.setdefault("QIYU_PARAM_PREPROBE_RESCUE", "1")
os.environ.setdefault("QIYU_MAX_CHEAP_PROBES", "120")
os.environ.setdefault("QIYU_MAX_HYP_PROBE", "96")

t0 = time.time()
from dual_engine_workflow_v2 import creation_blueprint as cb
from dual_engine_workflow_v2.research_discovery import _creation_skip_llm

print("skip_llm", _creation_skip_llm(None), flush=True)
out = cb.run_creation_blueprint(
    symbol=SYMBOL,
    timeframe=TF,
    direction=DIRECTION,
    brief=BRIEF,
    skip_llm=None,
    max_loops=1,
    out_dir=str(root / "alpha_discovery" / "validate_ma_cross_out"),
    run_id="validate_ma_cross_v2",
)
elapsed = time.time() - t0
st = out.get("stages") or {}
disc = st.get("research_discovery") or {}
meta = st.get("meta") or {}
design = (meta.get("design_doc") or {})
glm = (design.get("glm_enrichment") or {})

survivors = []
for s in (out.get("survivors") or disc.get("survivors") or []):
    if isinstance(s, dict):
        survivors.append(s)
h = disc.get("handoff") or out.get("handoff") or {}
state_counts = disc.get("research_state_counts") or {}

design_rows = []
for row in ((disc.get("population") or {}).get("hypotheses") or []):
    if str(row.get("source") or "").startswith(("structured_meta", "glm_meta", "architect")):
        design_rows.append(row)

best = None
for s in survivors:
    pr = s.get("probe") or {}
    hyp = s.get("hypothesis") or {}
    wr = pr.get("win_rate")
    mn = pr.get("mean_net")
    if wr is not None and float(wr) > 0.35 and mn is not None and float(mn) > 0:
        best = {
            "hypothesis_id": hyp.get("hypothesis_id"),
            "source": hyp.get("source"),
            "recipe_id": (s.get("recipe") or {}).get("recipe_id"),
            "win_rate": wr,
            "mean_net": mn,
            "terms": pr.get("terms"),
            "param_rescue": pr.get("param_rescue"),
        }
        break

report = {
    "brief": BRIEF,
    "elapsed_sec": round(elapsed, 1),
    "ok": out.get("ok"),
    "outcome": out.get("outcome"),
    "present_to_assembly": out.get("present_to_assembly") or disc.get("present_to_assembly"),
    "n_survivors": disc.get("n_survivors") or out.get("n_survivors"),
    "research_state_counts": state_counts,
    "nde_before_baseline": 63,
    "nde_after": state_counts.get("NO_DIRECTIONAL_EFFECT"),
    "meta": {
        "skip_llm": _creation_skip_llm(None),
        "glm_enriched": glm.get("enriched"),
        "glm_error": glm.get("error"),
        "selected_lens": (design.get("divergence") or {}).get("selected_lens_zh"),
        "n_design_perspectives": len((design.get("divergence") or {}).get("perspectives") or []),
    },
    "design_seed_hypotheses_n": len(design_rows),
    "design_seed_ids": [r.get("hypothesis_id") for r in design_rows[:8]],
    "qualified_candidate": best,
    "checklist": {
        "has_design_seed_candidate": bool(best and str(best.get("source") or "").startswith(
            ("structured_meta", "glm_meta", "architect")
        )),
        "has_any_survivor": bool(survivors),
        "wr_gt_35_mean_gt_0": best is not None,
        "nde_dropped": (
            state_counts.get("NO_DIRECTIONAL_EFFECT") is not None
            and state_counts.get("NO_DIRECTIONAL_EFFECT") < 63
        ),
    },
}
out_path = root / "alpha_discovery" / "CREATION_UPGRADE_VALIDATION_REPORT.json"
out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

md_lines = [
    "# 创造能力升级验证报告",
    "",
    "- **Brief**: %s" % BRIEF,
    "- **耗时**: %.1f 秒" % elapsed,
    "- **可装配**: %s" % report["present_to_assembly"],
    "- **存活数**: %s" % report["n_survivors"],
    "",
    "## 状态分布",
    "```json",
    json.dumps(state_counts, ensure_ascii=False, indent=2),
    "```",
    "",
    "## 元思考 / LLM",
    "- GLM 增强: %s" % glm.get("enriched"),
    "- 设计种子数: %s" % len(design_rows),
    "- 选中视角: %s" % report["meta"]["selected_lens"],
    "",
    "## 合格候选 (WR>35%% & mean_net>0)",
    "```json",
    json.dumps(best, ensure_ascii=False, indent=2, default=str) if best else "null",
    "```",
    "",
    "## 验收清单",
]
for k, v in report["checklist"].items():
    md_lines.append("- %s: **%s**" % (k, v))
(root / "alpha_discovery" / "CREATION_UPGRADE_VALIDATION_REPORT.md").write_text(
    "\n".join(md_lines), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
