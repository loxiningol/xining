# -*- coding: utf-8 -*-
import unittest
from unittest import mock

import auto_trade_formal_notify as notify
import auto_trade_strategy_titles as titles


class FormalNotifyTierWxTest(unittest.TestCase):
    def test_open_name_uses_macaron_not_letter_grade(self):
        name = notify.strategy_name_with_grade(
            "k", "ADA5顺势回升", {"strategy_tier": "MACARON"})
        self.assertEqual(name, "ADA5顺势回升（马卡龙策略）")
        self.assertNotIn("级", name)
        self.assertNotIn("A", name.split("（")[-1])

    def test_strips_legacy_b_suffix_for_daifuku(self):
        name = notify.strategy_name_with_tier(
            "k", "ADA5顺势回升（B级）", {"strategy_tier": "DAIFUKU"})
        self.assertEqual(name, "ADA5顺势回升（大福策略）")
        self.assertNotIn("B级", name)

    def test_open_card_has_macaron_prefix_and_no_sabc(self):
        captured = {}

        def fake_send(msg, kind=None, meta=None, dry_run=False):
            captured["msg"] = msg
            captured["kind"] = kind
            return {"ok": True, "sent": True}

        with mock.patch.object(notify, "send_message", side_effect=fake_send):
            notify.notify_open_success({
                "strategy_key": "k",
                "strategy_name": "ADA5顺势回升",
                "side": "long",
                "symbol": "XRP-USDT-SWAP",
                "strategy_tier": "MACARON",
                "opened_at": "2026-08-20 12:00:00",
            }, {})
        self.assertIn("📡 策略名称:", captured["msg"])
        self.assertNotIn("【开仓通报", captured["msg"])
        self.assertIn("ADA5顺势回升（马卡龙策略）", captured["msg"])
        self.assertIn("开仓标的: XRP", captured["msg"])
        self.assertNotIn("执行模式", captured["msg"])
        self.assertNotRegex(captured["msg"], r"[SABCDE]级")
        self.assertEqual(captured["kind"], "strategy_opened")

    def test_format_tier_hitch_zh(self):
        self.assertEqual(
            notify.format_tier_hitch_zh("马卡龙策略", "赔率型"),
            "马卡龙策略·赔率型")
        self.assertEqual(
            notify.format_tier_hitch_zh("大福策略", "方向型"),
            "大福策略·方向型")
        self.assertEqual(notify.format_tier_hitch_zh("马卡龙策略", None), "马卡龙策略")
        self.assertEqual(notify.format_tier_hitch_zh("手动开仓", "赔率型"), "手动开仓")
        self.assertEqual(
            notify.format_tier_hitch_zh("马卡龙策略·方向型", "赔率型"),
            "马卡龙策略·赔率型")

    def test_hitch_advice_by_class(self):
        self.assertEqual(
            notify.hitch_open_advice_text("kimi_6e2080e4b78b6362d5b42bc5"),
            "可以开顺风车仓（方向型）")
        self.assertEqual(
            notify.hitch_open_advice_text("kimi_c361a198338e20b159799485"),
            "不建议开顺风车单（赔率型）")
        self.assertEqual(
            notify.hitch_open_advice_text("k", hitch_class="方向型"),
            "可以开顺风车仓（方向型）")
        self.assertEqual(
            notify.hitch_open_advice_text("k", hitch_class="赔率型"),
            "不建议开顺风车单（赔率型）")
        self.assertEqual(
            notify.hitch_open_advice_text(""),
            "暂无分类，不建议开顺风车单")

    def test_hitch_close_type_line_omits_open_advice(self):
        self.assertEqual(
            notify.hitch_close_type_line("可以开顺风车仓（方向型）"),
            "🌦️ 策略类型方向型")
        self.assertEqual(
            notify.hitch_close_type_line("不建议开顺风车单（赔率型）"),
            "🌦️ 策略类型赔率型")
        self.assertEqual(
            notify.hitch_close_type_line(""),
            "🌦️ 策略类型未分类")
        msg = notify.format_close({
            "strategy_name": "测试",
            "side_label": "LONG / 做多",
            "close_type": "策略平仓",
            "sz": "1",
            "entry_price": "100",
            "close_price": "101",
            "pnl": "1",
            "ordId": "2",
            "time": "now",
            "hitch_advice": "可以开顺风车仓（方向型）",
        })
        self.assertTrue(msg.endswith("🌦️ 策略类型方向型"))
        self.assertNotIn("可以开顺风单", msg)
        self.assertNotIn("策略类型（方向型）", msg)

    def test_open_and_close_cards_include_hitch_advice(self):
        open_msg = notify.format_open_success({
            "strategy_name": "测试",
            "side_label": "LONG / 做多",
            "leverage": 20,
            "position_mode_label": "逐仓",
            "sz": "1",
            "price": "100",
            "stop_loss_pct_label": "0.9%",
            "stop_loss_price": "99.1",
            "ordId": "1",
            "time": "now",
            "hitch_advice": "可以开顺风车仓（方向型）",
        })
        close_msg = notify.format_close({
            "strategy_name": "测试",
            "side_label": "LONG / 做多",
            "close_type": "策略平仓",
            "sz": "1",
            "entry_price": "100",
            "close_price": "101",
            "pnl": "1",
            "ordId": "2",
            "time": "now",
            "hitch_advice": "不建议开顺风车单（赔率型）",
        })
        self.assertIn("🌦️ 策略类型（方向型）：可以开顺风单", open_msg)
        self.assertIn("🌦️ 策略类型赔率型", close_msg)
        self.assertNotIn("可以开顺风单", close_msg)
        self.assertNotIn("不建议开顺风单", close_msg)
        self.assertTrue(close_msg.endswith("🌦️ 策略类型赔率型"))
        self.assertTrue(close_msg.startswith("📡 策略名称:"))
        self.assertNotIn("顺风车开仓建议:", open_msg)
        self.assertNotIn("顺风车开仓建议:", close_msg)
        self.assertNotIn("小管家耳语", open_msg)
        self.assertNotIn("小管家耳语", close_msg)
        self.assertNotIn("栖语自动交易平仓通知", close_msg)
        self.assertNotIn("=== cosmic自动平仓通知 ===", close_msg)

    def test_losing_close_is_never_labeled_take_profit(self):
        msg = notify.format_close({
            "strategy_name": "白银1小时低波动带量收复做多（趋势过滤压缩版·优化）（马卡龙策略）",
            "side_label": "LONG / 做多",
            "close_type": "策略止盈",
            "close_reason": "strategy_take_profit_authoritative_exit",
            "sz": "993.0",
            "entry_price": "69.03",
            "close_price": "68.61",
            "pnl": "-4.1706",
            "ordId": "3857267339617636352",
            "time": "2026-08-23 12:05:27",
            "hitch_advice": "不建议开顺风车单（赔率型）",
        })
        self.assertIn("平仓类型: 策略规则退出", msg)
        self.assertNotIn("平仓类型: 策略止盈", msg)

    def test_invalidation_exit_keeps_real_type(self):
        self.assertEqual(
            notify.close_reason_from_exit_type("条件失效平仓"),
            "strategy_invalidated_exit")
        msg = notify.format_close({
            "strategy_name": "测试",
            "side_label": "LONG / 做多",
            "close_type": "条件失效平仓",
            "exit_type": "条件失效平仓",
            "close_reason": "strategy_invalidated_exit",
            "sz": "1",
            "entry_price": "69.03",
            "close_price": "68.61",
            "pnl": "-4.1706",
            "ordId": "1",
            "time": "now",
            "hitch_advice": "不建议开顺风车单（赔率型）",
        })
        self.assertIn("平仓类型: 条件失效平仓", msg)
        self.assertNotIn("策略止盈", msg)

    def test_winning_take_profit_label_unchanged(self):
        msg = notify.format_close({
            "strategy_name": "测试",
            "side_label": "LONG / 做多",
            "close_type": "策略止盈",
            "exit_type": "连续目标止盈",
            "sz": "1",
            "entry_price": "100",
            "close_price": "101",
            "pnl": "1",
            "ordId": "1",
            "time": "now",
            "hitch_advice": "可以开顺风车仓（方向型）",
        })
        self.assertIn("平仓类型: 连续目标止盈", msg)

    def test_close_sends_before_optimizer(self):
        order = []

        def fake_send(msg, kind=None, meta=None, dry_run=False):
            order.append("send")
            return {"ok": True, "sent": True, "msg": msg}

        def fake_note(closed):
            order.append("opt")

        with mock.patch.object(notify, "send_message", side_effect=fake_send), \
                mock.patch(
                    "auto_trade_strategy_dynamic_optimizer.note_closed_trade",
                    side_effect=fake_note):
            notify.notify_close({
                "strategy_key": "k",
                "strategy_name": "N",
                "side": "long",
                "strategy_tier": "DAIFUKU",
                "closed_at": "2026-08-20 12:00:00",
            })
        self.assertEqual(order, ["send", "opt"])

    def test_rewrite_scrubs_legacy_letter_grade(self):
        out = titles.rewrite_strategy_keys_in_text("名称: ADA5顺势回升（B级）")
        self.assertNotIn("B级", out)
        self.assertIn("ADA5顺势回升", out)

    def test_wx_summary_splits_quality_trade_and_safety_net(self):
        self.assertEqual(
            notify._wx_summary("kimi_creation_quality_gate_success"),
            "cosmic策略质检与上线通知")
        self.assertEqual(
            notify._wx_summary("strategy_pending_confirm"),
            "cosmic策略质检与上线通知")
        self.assertEqual(
            notify._wx_summary("strategy_tier_online"),
            "cosmic策略质检与上线通知")
        self.assertEqual(
            notify._wx_summary("strategy_opened"), "cosmic自动开仓/平仓通知")
        self.assertEqual(
            notify._wx_summary("strategy_closed"), "cosmic自动开仓/平仓通知")
        self.assertEqual(
            notify._wx_summary("strategy_open_failed"), "cosmic自动开仓/平仓通知")
        self.assertEqual(
            notify._wx_summary("strategy_open_skipped"), "cosmic自动开仓/平仓通知")
        self.assertEqual(
            notify._wx_summary("formal_auto_trade"), "cosmic自动开仓/平仓通知")
        self.assertEqual(
            notify._wx_summary("protective_close_attempted"),
            "cosmic自动开仓/平仓通知")
        self.assertEqual(
            notify._wx_summary("force_protect_session_open"),
            "cosmic自动开仓/平仓通知")
        self.assertEqual(
            notify._wx_summary("force_protect_session_close"),
            "cosmic自动开仓/平仓通知")
        self.assertEqual(
            notify._wx_summary("force_protect_auto_close_repark"),
            notify.WX_SUMMARY_AUTO_TRADE)
        self.assertEqual(
            notify._wx_summary("force_protect_intervene"),
            "cosmic风险控制系统通知")
        self.assertEqual(
            notify._wx_summary("day_lock"),
            "cosmic风险控制系统通知")
        self.assertEqual(
            notify._wx_summary("inbound_block"),
            "cosmic风险控制系统通知")
        self.assertEqual(
            notify._wx_summary("force_protect_manual_quota"),
            "cosmic风险控制系统通知")
        self.assertEqual(
            notify._wx_summary("force_protect_manual_size_warn"),
            "cosmic风险控制系统通知")
        self.assertEqual(
            notify._wx_summary("hold_path_warning"),
            "cosmic智能辅助系统")
        self.assertEqual(
            notify._wx_summary("hold_path_urgent"),
            "cosmic智能辅助系统")
        self.assertEqual(notify._wx_summary("daily_auto_trade_report"), "cosmic通知")
        self.assertEqual(notify._wx_summary(None), "cosmic通知")

    def test_open_failed_risk_cap_uses_zh_reason(self):
        msg = notify.format_open_failed({
            "strategy_name": "趋势回调买入优化V5（马卡龙策略）",
            "side_label": "LONG / 做多",
            "symbol_label": "BTC",
            "leverage": "10.24",
            "position_mode_label": "逐仓",
            "error": "portfolio estimated risk limit exceeded",
            "time": "2026-08-22 18:01:45",
        })
        self.assertIn("失败原因: 自动开仓同时估算风险达到阈值", msg)
        self.assertNotIn("portfolio estimated risk limit exceeded", msg)

    def test_open_skipped_card_has_occupancy_and_symbol(self):
        msg = notify.format_open_skipped({
            "strategy_name": "测试策略（马卡龙策略）",
            "side_label": "LONG / 做多",
            "symbol_label": "SOL",
            "leverage": "20",
            "position_mode_label": "逐仓",
            "tier_need_text": "马卡龙策略 50%",
            "occupancy_text": "马卡龙策略 XAU 50% + 大福策略 ETH 25% = 75%",
            "leftover_text": "仅大福",
            "error": "资金占用已满，本策略档位无法按原比例开仓",
            "time": "now",
        })
        self.assertTrue(msg.startswith("📡 策略名称:"))
        self.assertNotIn("栖语自动交易开仓跳过通知", msg)
        self.assertIn("开仓标的: SOL", msg)
        self.assertIn("本策略档位: 马卡龙策略 50%", msg)
        self.assertIn("剩余可开: 仅大福", msg)
        self.assertNotIn("开仓失败", msg)

    def test_occupancy_skip_cooldown_same_fingerprint(self):
        import tempfile
        from pathlib import Path
        tmp = tempfile.mkdtemp()
        skip_path = Path(tmp) / "occupancy_skip_notify.json"
        payload = {
            "strategy_key": "k1",
            "symbol": "SOL-USDT-SWAP",
            "occupancy_fingerprint": "XAU:0.50,ETH:0.25|need:0.50|SOL-USDT-SWAP",
        }
        with mock.patch.object(notify, "SKIP_NOTIFY_FILE", skip_path):
            first = notify.occupancy_skip_due(payload, now_ts=1000.0)
            second = notify.occupancy_skip_due(payload, now_ts=1100.0)
            later = notify.occupancy_skip_due(payload, now_ts=1000.0 + 3601)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(later)

    def test_demote_c_card_is_hard_blocked(self):
        captured = {}

        def fake_send(*args, **kwargs):
            captured["called"] = True
            return {"ok": True, "sent": True}

        with mock.patch.object(notify, "_send_via_verified_wxpusher",
                               side_effect=fake_send), mock.patch.object(
                notify, "_audit", return_value={"blocked": True}):
            out = notify.send_message(
                "【RSI超买高位动能未完全转折空 降级至C级】\n"
                "原因: 首3单2止损\n仓位: C级 15%",
                kind="strategy_downgrade_c",
            )
        self.assertTrue(out.get("blocked"))
        self.assertFalse(out.get("sent"))
        self.assertFalse(captured.get("called"))

    def test_legacy_pending_optimize_card_is_hard_blocked(self):
        captured = {}

        def fake_send(*args, **kwargs):
            captured["called"] = True
            return {"ok": True, "sent": True}

        with mock.patch.object(notify, "_send_via_verified_wxpusher",
                               side_effect=fake_send), mock.patch.object(
                notify, "_audit", return_value={"blocked": True}):
            out = notify.send_message(
                "【四复核通过·进入策略待优化板块】\n"
                "名称: 中期空头趋势反弹受阻下沿破位顺趋势做空（INTC 1h）",
                kind="strategy_pending_confirm",
            )
        self.assertTrue(out.get("blocked"))
        self.assertFalse(out.get("sent"))
        self.assertEqual("retired_pending_optimize_wx", out.get("reason"))
        self.assertFalse(captured.get("called"))

    def test_quality_pass_human_confirm_card_is_hard_blocked(self):
        captured = {}

        def fake_send(*args, **kwargs):
            captured["called"] = True
            return {"ok": True, "sent": True}

        with mock.patch.object(notify, "_send_via_verified_wxpusher",
                               side_effect=fake_send), mock.patch.object(
                notify, "_audit", return_value={"blocked": True}):
            out = notify.send_message(
                "【质检通过·待人工确认】\n"
                "名称: 中期空头趋势反弹受阻下沿破位顺趋势做空（INTC 1h）\n"
                "状态: 等待人工确认，未进入实盘",
                kind="strategy_pending_confirm",
            )
        self.assertTrue(out.get("blocked"))
        self.assertFalse(out.get("sent"))
        self.assertEqual("retired_three_ai_confirm_wx", out.get("reason"))
        self.assertFalse(captured.get("called"))

    def test_three_ai_ops_digest_is_hard_blocked(self):
        captured = {}

        def fake_send(*args, **kwargs):
            captured["called"] = True
            return {"ok": True, "sent": True}

        with mock.patch.object(notify, "_send_via_verified_wxpusher",
                               side_effect=fake_send), mock.patch.object(
                notify, "_audit", return_value={"blocked": True}):
            out = notify.send_message(
                "【三AI运维短评】\n"
                "deepseek:调用失败 / qwen:守护进程异常未运行 / glm:HTTP Error 429",
                kind="system_health_ai_digest",
            )
        self.assertTrue(out.get("blocked"))
        self.assertFalse(out.get("sent"))
        self.assertEqual("retired_three_ai_ops_digest_wx", out.get("reason"))
        self.assertFalse(captured.get("called"))

    def test_hitch_follow_close_uses_hold_assist_summary(self):
        self.assertEqual(
            notify._wx_summary("force_protect_hitch_follow_close"),
            notify.WX_SUMMARY_HOLD_ASSIST)
        self.assertEqual(
            notify._wx_summary("force_protect_intervene"),
            notify.WX_SUMMARY_SAFETY_NET)

    def test_hitch_class_stats_sl_rate_makes_odds(self):
        trades = [
            {"net_return": -0.02, "exit_reason": "protective_stop",
             "maximum_adverse_excursion": 0.02, "maximum_favorable_excursion": 0.001}
            for _ in range(4)
        ] + [
            {"net_return": 0.04, "exit_reason": "take_profit",
             "maximum_adverse_excursion": 0.004, "maximum_favorable_excursion": 0.04}
            for _ in range(6)
        ]
        stats = notify.hitch_class_stats(trades, 0.02)
        self.assertGreaterEqual(stats["sl_rate"], 0.30)
        self.assertEqual(notify.HITCH_CLASS_ODDS, stats["klass"])

    def test_hitch_class_stats_low_sl_is_direction(self):
        trades = [
            {"net_return": 0.03, "exit_reason": "take_profit",
             "maximum_adverse_excursion": 0.003, "maximum_favorable_excursion": 0.03}
            for _ in range(8)
        ] + [
            {"net_return": -0.01, "exit_reason": "timeout",
             "maximum_adverse_excursion": 0.004, "maximum_favorable_excursion": 0.008}
            for _ in range(2)
        ]
        stats = notify.hitch_class_stats(trades, 0.02)
        self.assertLess(stats["sl_rate"], 0.22)
        self.assertEqual(notify.HITCH_CLASS_DIRECTION, stats["klass"])

    def test_hitch_class_stats_high_invalidation_is_odds(self):
        trades = [
            {"net_return": 0.02, "exit_reason": "invalidation",
             "maximum_adverse_excursion": 0.003, "maximum_favorable_excursion": 0.02}
            for _ in range(5)
        ] + [
            {"net_return": 0.01, "exit_reason": "take_profit",
             "maximum_adverse_excursion": 0.002, "maximum_favorable_excursion": 0.01}
            for _ in range(5)
        ]
        stats = notify.hitch_class_stats(trades, 0.02)
        self.assertGreaterEqual(stats["inv_rate"], 0.40)
        self.assertEqual(notify.HITCH_CLASS_ODDS, stats["klass"])

    def test_semantic_hair_trigger_invalidation(self):
        issues = notify.semantic_direction_issues({
            "name": "前低破位做空",
            "exit_plan": {"conditional_exits": [{
                "role": "invalidation",
                "when": {"feature": "close_z_20", "op": "gt", "value": 1.5},
            }]},
        })
        self.assertTrue(any("close_z_20" in x for x in issues))

    def test_semantic_reversal_name_is_not_direction(self):
        issues = notify.semantic_direction_issues({
            "name": "CCI负值流动性扫荡后多头reclaim反转",
            "exit_plan": {"conditional_exits": []},
        })
        self.assertTrue(any(x.startswith("mean_reversion_name:") for x in issues))

    def test_hair_trigger_overrides_direction_freeze(self):
        key = "kimi_6e2080e4b78b6362d5b42bc5"
        self.assertEqual("方向型", notify.HITCH_CLASS_BY_KEY[key])
        fake = {
            "key": key,
            "name": "趋势延续",
            "exit_plan": {"conditional_exits": [{
                "role": "invalidation",
                "when": {"feature": "close_z_20", "op": "gt", "value": 1.5},
            }]},
        }
        with mock.patch.object(notify, "load_semantic_strategy", return_value=fake):
            self.assertEqual("赔率型", notify.strategy_hitch_class(key))


if __name__ == "__main__":
    unittest.main()
