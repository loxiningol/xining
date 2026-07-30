# -*- coding: utf-8 -*-
import unittest

import auto_trade_strategy_titles as titles


class StrategyTitlesWxTest(unittest.TestCase):
    def test_short_title_strips_0725_suffix(self):
        shown = titles.short_strategy_title(
            "codex0725_ada5_trendpb_r42_z2p0_h14",
            "ADA5趋势回踩·0725GLM",
        )
        self.assertEqual(shown, "ADA5趋势回踩")

    def test_rewrite_key_line(self):
        raw = (
            "【7.25训练1产出】\n"
            "策略已两票通过并进入人工确认队列（ChatGPT忽略）\n"
            "Key: codex0725_ada5_trendpb_r42_z2p0_h14\n"
            "时间: 2026-07-25 04:16:05"
        )
        out = titles.rewrite_strategy_keys_in_text(raw)
        self.assertNotIn("codex0725_ada5_trendpb_r42_z2p0_h14", out)
        self.assertIn("ADA5趋势回踩", out)
        self.assertNotIn("Key:", out)

    def test_preserve_confirm_cli(self):
        raw = (
            "确认指令: python3 auto_trade_human_confirm_pipeline.py "
            "--confirm codex0725_ada5_trendpb_r42_z2p0_h14"
        )
        out = titles.rewrite_strategy_keys_in_text(raw)
        self.assertIn("--confirm codex0725_ada5_trendpb_r42_z2p0_h14", out)

    def test_ada_t3_short_name(self):
        self.assertEqual(
            titles.resolve_strategy_name(
                "codex0725t3_ada5m_trendpb_r42_z2p3_h14",
                "ADA5顺势回升·0725T3",
            ),
            "ADA5顺势回升",
        )
        self.assertEqual(
            titles.short_strategy_title(
                "codex0725t3_ada5m_trendpb_r42_z2p3_h14",
                "ADA5顺势回升·0725T3",
            ),
            "ADA5顺势回升",
        )

    def test_rewrite_scrubs_legacy_long_ada_title(self):
        raw = "名称: ADA5顺势回升·0725T3\nKey: codex0725t3_ada5m_trendpb_r42_z2p3_h14"
        out = titles.rewrite_strategy_keys_in_text(raw)
        self.assertNotIn("0725T3", out)
        self.assertIn("ADA5顺势回升", out)

    def test_live_strategy_card_actual_single_pnl(self):
        card = titles.format_live_strategy_card(
            "ltc5_exhaustion_fade_short_ai",
            "LTC 5分钟冲高衰竭回落",
            grade="A",
            max_position_ratio=0.15,
            ai_theoretical_wr_avg=70.0,
            actual_single_trade_pnl_pct=16.21,
        )
        self.assertIn("实际单笔盈利率 +16.2%", card)
        self.assertNotIn("预期单笔", card)

    def test_unrated_defaults_to_b(self):
        card = titles.format_live_strategy_card(
            "ltc5_exhaustion_fade_short_ai",
            "LTC 5分钟冲高衰竭回落",
            grade="未评级",
            max_position_ratio=0.15,
            ai_theoretical_wr_avg=60.5,
        )
        self.assertIn("\nB\n", "\n" + card + "\n")
        self.assertNotIn("未评级", card)
        self.assertNotIn("待评级", card)
        self.assertIn("三AI理论胜率 60.5%", card)
        self.assertIn("仓位 15%", card)

    def test_shadow_card_and_key_fallback(self):
        card = titles.format_live_strategy_card(
            "ada5_z20_t60_prev_h14_0724k",
            "ada5_z20_t60_prev_h14_0724k",
            grade="shadow",
            max_position_ratio=0.0,
            ai_theoretical_wr_avg=74.667,
        )
        self.assertIn("ADA 5分钟 H1趋势回踩续涨", card)
        self.assertIn("SHADOW", card)
        self.assertIn("仓位 0%", card)
        self.assertIn("三AI理论胜率 74.7%", card)
        self.assertNotIn("ada5_z20_t60_prev_h14_0724k", card)


if __name__ == "__main__":
    unittest.main()
