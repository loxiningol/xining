# -*- coding: utf-8 -*-
import unittest
from unittest import mock

import auto_trade_hold_assist_gate as gate
import auto_trade_hold_assist_guidance as gd
import auto_trade_hold_structure as hs
import auto_trade_hold_tribunal as ht


def _ctx(**kwargs):
    row = {
        "pnl": "profit",
        "p_take_profit": 0.375,
        "p_stop": 0.625,
        "p_timed": 0.0,
        "giveback_frac": 0.46,
        "has_trailing": True,
        "trail_armed": False,
        "trail_near": False,
        "counterfactual_zh": "若人工不动，本策略下一步只会撞止盈位/保护止损/定时之一；当前先触更可能是止损。",
    }
    row.update(kwargs)
    return row


class HoldGuidanceUnitTest(unittest.TestCase):
    def test_lock_only_if_profit(self):
        allowed = gd.allowed_actions(_ctx(pnl="profit"))
        self.assertIn(gd.LOCK_PROFIT, allowed)
        self.assertNotIn(gd.CUT_LOSS, allowed)
        loss = gd.allowed_actions(_ctx(pnl="loss", giveback_frac=0.1))
        self.assertIn(gd.CUT_LOSS, loss)
        self.assertNotIn(gd.LOCK_PROFIT, loss)

    def test_wait_trail_only_when_near_and_not_armed(self):
        armed = gd.allowed_actions(_ctx(trail_armed=True, trail_near=False))
        self.assertNotIn(gd.WAIT_TRAIL, armed)
        far = gd.allowed_actions(_ctx(trail_armed=False, trail_near=False))
        self.assertNotIn(gd.WAIT_TRAIL, far)
        near = gd.allowed_actions(_ctx(trail_armed=False, trail_near=True))
        self.assertIn(gd.WAIT_TRAIL, near)

    def test_watch_giveback_needs_elevated_giveback(self):
        low = gd.allowed_actions(_ctx(giveback_frac=0.10))
        self.assertNotIn(gd.WATCH_GIVEBACK, low)
        high = gd.allowed_actions(_ctx(giveback_frac=0.46))
        self.assertIn(gd.WATCH_GIVEBACK, high)

    def test_amend_stop_rejected(self):
        out = gd.sanitize({
            "action": gd.LOCK_PROFIT,
            "guidance_zh": "建议改止损到78000",
            "evidence_hash": "h",
        }, _ctx(), expected_hash="h")
        self.assertEqual(out["action"], gd.NO_CALL)
        self.assertFalse(out["ok"])
        self.assertIn("amend", out["error"])

    def test_auto_close_rejected(self):
        out = gd.sanitize({
            "action": gd.LOCK_PROFIT,
            "guidance_zh": "立即平仓",
            "evidence_hash": "h",
        }, _ctx(), expected_hash="h")
        self.assertEqual(out["action"], gd.NO_CALL)
        self.assertIn("auto_close", out["error"])

    def test_odds_contradiction_rejected(self):
        out = gd.sanitize({
            "action": gd.HOLD_TO_PLAN,
            "guidance_zh": "止损 90% 所以继续拿",
            "evidence_hash": "h",
        }, _ctx(), expected_hash="h")
        self.assertEqual(out["error"], "odds_contradiction")

    def test_lock_accepted_on_profit(self):
        out = gd.sanitize({
            "action": gd.LOCK_PROFIT,
            "guidance_zh": "先触仍更可能是保护止损；追踪未激活。",
            "evidence_hash": "h",
        }, _ctx(), expected_hash="h")
        self.assertTrue(out["ok"])
        self.assertEqual(out["action"], gd.LOCK_PROFIT)
        self.assertIn("建议人工了结锁定利润", out["guidance_zh"])
        self.assertIn("系统不改保护止损", out["guidance_zh"])

    def test_cut_loss_does_not_duplicate_skeleton(self):
        phrase = "建议人工了结，避免把剩余路径交给保护止损"
        out = gd.sanitize({
            "action": gd.CUT_LOSS,
            "guidance_zh": (
                phrase + "；" + phrase + "；当前先触更可能是止损。"
            ),
            "evidence_hash": "h",
        }, _ctx(pnl="loss"), expected_hash="h")
        self.assertTrue(out["ok"])
        self.assertEqual(out["guidance_zh"].count(phrase), 1)
        self.assertIn("当前先触更可能是止损", out["guidance_zh"])

    def test_cut_loss_strips_counterfactual_paste(self):
        out = gd.sanitize({
            "action": gd.CUT_LOSS,
            "guidance_zh": (
                "建议人工了结，避免把剩余路径交给保护止损；"
                "若人工不动，本策略下一步只会撞止盈位/保护止损/定时之一；"
                "当前先触更可能是止损。"
            ),
            "evidence_hash": "h",
        }, _ctx(pnl="loss"), expected_hash="h")
        self.assertTrue(out["ok"])
        self.assertNotIn("若人工不动", out["guidance_zh"])
        self.assertIn("当前先触更可能是止损", out["guidance_zh"])

    def test_hash_mismatch_no_call(self):
        out = gd.sanitize({
            "action": gd.HOLD_TO_PLAN,
            "guidance_zh": "建议继续交给本策略退出计划。",
            "evidence_hash": "bad",
        }, _ctx(), expected_hash="good")
        self.assertEqual(out["error"], "evidence_hash_mismatch")
        self.assertEqual(out["action"], gd.NO_CALL)

    def test_reason_zh_still_bans_action_verbs(self):
        out = gate.quality_gate({
            "p_take_profit": 0.375, "p_stop": 0.625,
            "reason_zh": "建议立即平仓锁定利润",
            "evidence_hash": "h",
            "cited_tools": ["giveback_dynamics", "swing", "volume"],
        }, expected_hash="h")
        self.assertIn("forbidden_action", out["reasons"])
        ok = gd.sanitize({
            "action": gd.LOCK_PROFIT,
            "guidance_zh": "建议人工了结锁定利润；系统不改保护止损。",
            "evidence_hash": "h",
        }, _ctx(), expected_hash="h")
        self.assertTrue(ok["ok"])
        self.assertIn("建议人工了结", ok["guidance_zh"])
        self.assertNotIn("改止损", ok["guidance_zh"])

    def test_counterfactual_mentions_barriers(self):
        text = gd.counterfactual_zh(
            {"take_profit_price": 81246, "stop": 77746},
            {"p_take_profit": 0.375, "p_stop": 0.625, "p_timed": 0.0},
        )
        self.assertIn("若人工不动", text)
        self.assertIn("保护止损", text)
        self.assertIn("止损", text)


class HoldGuidanceWxTest(unittest.TestCase):
    def test_message_has_three_lines_when_guided(self):
        msg = ht.format_warning_message({
            "p_take_profit": 0.375,
            "p_stop": 0.625,
            "reason_zh": "同桶18笔仅对照；主看回吐6根",
            "strategy_title": "趋势回调买入优化V5",
            "symbol_label": "BTC",
            "direction_zh": "做多",
            "guidance_zh": "建议人工了结锁定利润；系统不改保护止损。先触仍更可能是保护止损；追踪未激活。",
        })
        self.assertIn("估算概率：止盈", msg)
        self.assertIn("逻辑推理", msg)
        self.assertIn("智能指引", msg)
        self.assertIn("建议人工了结锁定利润", msg)
        self.assertNotIn("先盈", msg)
        self.assertNotIn("先亏", msg)

    def test_message_stays_two_lines_without_guidance(self):
        msg = ht.format_warning_message({
            "p_take_profit": 0.28,
            "p_stop": 0.72,
            "reason_zh": "保护性止损12笔",
            "strategy_title": "白银1小时低波动带量收复做多（马卡龙）",
            "symbol_label": "XAG",
            "direction_zh": "做多",
        })
        self.assertIn("逻辑推理", msg)
        self.assertNotIn("智能指引", msg)

    def test_guidance_failure_does_not_change_odds(self):
        path = {
            "ok": True, "voter": "remaining_path",
            "p_take_profit": 0.375, "p_stop": 0.625, "p_timed": 0.0,
            "reason_zh": "止盈位 81246；保护止损 77746。",
        }
        vote = hs.author_remaining_path(
            {"ok": True}, {"ok": True, "methods": []},
            {"ok": True, "reason_zh": "回吐与动能不足"},
            path_vote=path,
        )
        self.assertEqual(vote["p_take_profit"], 0.375)
        self.assertEqual(vote["p_stop"], 0.625)
        checked = gate.quality_guidance(
            {"ok": False, "error": "timeout"},
            _ctx(), expected_hash="h",
        )
        self.assertEqual(vote["p_take_profit"], 0.375)
        self.assertEqual(checked["action"], gd.NO_CALL)


class HoldGuidanceAskTest(unittest.TestCase):
    def test_maybe_ask_guidance_does_not_write_odds(self):
        called = []

        def ask(name, evidence):
            called.append(evidence)
            return {
                "ok": True,
                "action": "lock_profit_manual",
                "guidance_zh": "追踪未激活",
                "evidence_hash": "h",
                "p_take_profit": 0.99,
                "p_stop": 0.01,
            }

        before_ds = dict(hs._DS_LAST)
        out = hs.maybe_ask_guidance(
            "pos", _ctx(), [gd.LOCK_PROFIT, gd.HOLD_TO_PLAN, gd.NO_CALL],
            evidence_hash="h", ask_fn=ask,
        )
        self.assertEqual(len(called), 1)
        self.assertIn(gd.LOCK_PROFIT, called[0]["required_json"]["action"])
        self.assertNotIn("p_take_profit", called[0]["required_json"])
        self.assertEqual(dict(hs._DS_LAST), before_ds)
        checked = gd.sanitize(out, _ctx(), expected_hash="h")
        self.assertEqual(checked["action"], gd.LOCK_PROFIT)
        self.assertNotEqual(checked.get("p_take_profit"), 0.99)


class HoldGuidanceSurfaceTest(unittest.TestCase):
    def test_board_item_keeps_guidance_for_watch(self):
        item = ht._hold_board_item("k", {
            "strategy_title": "趋势回调买入优化V5",
            "symbol_label": "BTC",
            "direction_zh": "做多",
        }, {
            "last_p_take_profit": 0.375,
            "last_p_stop": 0.625,
            "last_p_timed": 0.0,
            "last_reason_zh": "回吐与动能不足",
            "last_guidance_zh": "建议人工了结锁定利润；系统不改保护止损。",
            "last_guidance_action": gd.LOCK_PROFIT,
            "last_guidance_ok": True,
            "last_tier": None,
        })
        self.assertEqual(item["status"], "watch")
        self.assertIn("建议人工了结锁定利润", item["guidance_zh"])
        self.assertEqual(item["guidance_action"], gd.LOCK_PROFIT)

    def test_clock_slot_does_not_ask_guidance(self):
        kernel = {
            "ok": True, "voter": "path_kernel",
            "p_stop": 0.62, "p_take_profit": 0.38,
        }
        struct = {"ok": True, "methods": ["giveback"], "last_close_ts": 1700000000000}
        snap = {
            "strategy_key": "k", "side": "long",
            "entry_price": 80000.0, "mark_price": 80500.0,
            "stop_loss_price": 77746.0,
            "timeframe": "1h", "candles": [],
            "_ask_api": False,
        }
        guide_calls = []

        def ask_ds(*a, **k):
            raise AssertionError("explain pass must not run off the clock slot")

        def ask_g(*a, **k):
            guide_calls.append(1)
            return {"ok": True, "action": gd.LOCK_PROFIT}

        with mock.patch("auto_trade_hold_path_kernel.estimate_position", return_value=kernel):
            with mock.patch.object(hs, "detect_structure", return_value=struct):
                with mock.patch.object(hs, "maybe_ask_deepseek", side_effect=ask_ds):
                    with mock.patch.object(hs, "maybe_ask_guidance", side_effect=ask_g):
                        vote = ht.default_estimate(
                            {"position_id": "p", "strategy_key": "k", "side": "long"},
                            dict(snap),
                        )
        self.assertEqual(guide_calls, [])
        self.assertFalse(vote.get("guidance_ok"))
        self.assertIsNone(vote.get("guidance_zh"))
        self.assertEqual(vote.get("skip_reason"), "clock_slot")


if __name__ == "__main__":
    unittest.main()
