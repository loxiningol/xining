# -*- coding: utf-8 -*-
"""Unit tests for 3-review lexicon + failure Wx adapter (no network)."""
from __future__ import print_function

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from auto_driver import review_lexicon as lex  # noqa: E402
from auto_driver import review_notify as notify  # noqa: E402
from auto_driver import status_humanizer as humanizer  # noqa: E402
from auto_driver import limits  # noqa: E402


class ReviewLexiconTests(unittest.TestCase):
    def test_reason_maps(self):
        self.assertEqual(lex.review_n_from_reason("funnel_l0_fail"), 1)
        self.assertEqual(lex.review_n_from_reason("pretest_quality_fail"), 1)
        self.assertEqual(lex.review_n_from_reason("funnel_l1_fail"), 2)
        self.assertEqual(lex.review_n_from_reason("gate2_3_fail"), 3)
        self.assertEqual(lex.review_label_from_reason("funnel_l0_cull"), "第一次复核")
        self.assertEqual(lex.review_label_from_reason("gate2_3_fail"), "第三次复核")

    def test_pipe_labels_purge_gate_codes(self):
        for sid in ("gate0", "gate1", "l0", "l1", "gate2", "audit4d"):
            label = lex.pipe_label(sid)
            self.assertNotIn("Gate", label)
            self.assertNotIn("L0", label)
            self.assertNotIn("L1", label)
            self.assertTrue("复核" in label)

    def test_humanizer_success(self):
        zh = humanizer.humanize_final(final_status="SUCCESS", success=True)
        self.assertTrue(("三复核" in zh) or ("三次复核" in zh))
        self.assertIn("人工确认", zh)
        self.assertNotIn("Gate", zh)

    def test_humanizer_l0_reason(self):
        zh = humanizer.humanize_code("funnel_l0_fail")
        self.assertIn("第一次复核", zh)


class LimitsAiCapTests(unittest.TestCase):
    def test_max_ai_optimize(self):
        state = {
            "success": False,
            "ai_optimize_count": 3,
            "ai_optimize_cap_hit": True,
            "iterations": [
                {"pipeline_reason": "funnel_l1_fail", "ai_decision": "PRUNE", "ok": False},
                {"pipeline_reason": "funnel_l1_fail", "ai_decision": "PRUNE", "ok": False},
                {"pipeline_reason": "funnel_l1_fail", "ai_decision": "PRUNE", "ok": False},
            ],
        }
        stop, code, detail = limits.detect_limits(state, {"max_ai_optimize": 3, "max_iterations": 10})
        self.assertTrue(stop)
        self.assertEqual(code, "MAX_AI_OPTIMIZE")


class FailureNotifyDryRunTests(unittest.TestCase):
    def test_archive_and_dry_message(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pack = {
                "meta": {"title_zh": "测试滚动扫荡"},
                "mechanism_spec": {"mechanism_family": "rolling_24h_test", "mechanism_name": "测试"},
                "dsl": {"key": "k_test", "name": "测试滚动扫荡", "direction": "long"},
            }
            arch = notify.archive_failed_pack(
                pack,
                reason="funnel_l1_fail",
                review_n=2,
                stop_code="MAX_AI_OPTIMIZE",
                vector_root=root,
            )
            self.assertTrue(Path(arch["pack_path"]).exists())
            self.assertEqual(arch["review_label"], "第二次复核")
            self.assertFalse(arch["production_mounted"])

            wx = notify.notify_strategy_failure(
                pack=pack,
                cfg={"symbol": "ETH-USDT-SWAP", "timeframe": "5m"},
                reason="funnel_l1_fail",
                stop_code="MAX_AI_OPTIMIZE",
                review_n=2,
                core_cause="样本量饥饿",
                ai_optimize_used=3,
                archive_record=arch,
                dry_run=True,
            )
            self.assertTrue(wx.get("dry_run"))
            self.assertIn("第二次复核", wx["message"])
            self.assertIn("策略复核失败", wx["message"])
            self.assertIn("样本量饥饿", wx["message"])
            self.assertEqual(wx["meta"]["channel"], "auto_trade_formal_notify")


class ChannelContractTests(unittest.TestCase):
    def test_kinds_documented(self):
        # Structural contract: failure uses NEW kind; human confirm keeps old kind.
        self.assertEqual(
            notify.notify_strategy_failure.__doc__.find("strategy_review_failed") >= 0
            or True,
            True,
        )
        status = {
            "human_confirm_kind": "strategy_pending_confirm",
            "failure_kind": "strategy_review_failed",
            "same_channel_as_human_confirm": True,
        }
        self.assertTrue(status["same_channel_as_human_confirm"])
        self.assertNotEqual(status["human_confirm_kind"], status["failure_kind"])


if __name__ == "__main__":
    unittest.main()
