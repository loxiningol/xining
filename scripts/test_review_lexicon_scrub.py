# -*- coding: utf-8 -*-
"""Ensure Gate/L codes are scrubbed from user/AI-facing surfaces."""
from __future__ import print_function

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from dual_engine_workflow_v2.review_lexicon import (  # noqa: E402
    REVIEW_1, REVIEW_2, REVIEW_3, REVIEW_1_FULL, REVIEW_2_FULL, REVIEW_3_FULL,
    scrub, pipe_label,
)
from auto_driver import status_humanizer as humanizer  # noqa: E402


class LexiconContractTests(unittest.TestCase):
    def test_full_titles(self):
        self.assertIn("基础语法", REVIEW_1_FULL)
        self.assertIn("单标的历史回测", REVIEW_2_FULL)
        self.assertIn("多标的矩阵", REVIEW_3_FULL)

    def test_scrub_gate_l(self):
        s = scrub("Gate0/Gate1/L0 then L1 then Gate2/L2/L3")
        for bad in ("Gate0", "Gate1", "Gate2", "L0", "L1", "L2", "L3"):
            self.assertNotIn(bad, s)
        self.assertIn(REVIEW_1, s)
        self.assertIn(REVIEW_2, s)
        self.assertIn(REVIEW_3, s)

    def test_scrub_funnel_reasons(self):
        s = scrub("funnel_l0_fail / gate2_3_fail")
        self.assertNotIn("funnel_l0", s.lower().replace("第一次", ""))
        self.assertIn(REVIEW_1, s)
        self.assertIn(REVIEW_3, s)

    def test_humanizer_no_gate_leak(self):
        for code in ("funnel_l0_fail", "funnel_l1_fail", "gate2_3_fail", "Gate2", "L0", "L1"):
            zh = humanizer.humanize_code(code)
            self.assertNotIn("Gate", zh)
            self.assertNotIn("L0", zh)
            self.assertNotIn("L1", zh)

    def test_pipe_labels(self):
        for sid in ("gate0", "gate1", "l0", "l1", "gate2", "audit4d"):
            lab = pipe_label(sid)
            self.assertNotIn("Gate", lab)
            self.assertNotIn("L0", lab)
            self.assertNotIn("L1", lab)
            self.assertIn("复核", lab)


if __name__ == "__main__":
    unittest.main()
