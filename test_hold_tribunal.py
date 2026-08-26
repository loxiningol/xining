# -*- coding: utf-8 -*-
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import auto_trade_formal_notify as notify
import auto_trade_hold_tribunal as ht


def _pos(**kwargs):
    row = {
        "position_id": "XAG-USDT-SWAP|long|isolated",
        "inst_id": "XAG-USDT-SWAP",
        "symbol_label": "XAG",
        "side": "long",
        "direction_zh": "做多",
        "manual": False,
        "source": "formal_auto_trade",
        "strategy_key": "kimi_xag_macaron",
        "strategy_title": "白银1小时低波动带量收复做多（马卡龙）",
        "entry_price": 69.20,
        "mark_price": 69.03,
        "leverage": 7.56,
        "contracts": 912,
        "mgn_mode": "isolated",
        "timeframe": "1h",
        "profit_amount_usdt": -1.55,
    }
    row.update(kwargs)
    return row


def _local(entry=69.20, stop=68.10, pct=0.016):
    return {
        "symbol": "XAG-USDT-SWAP",
        "side": "long",
        "timeframe": "1h",
        "strategy_key": "kimi_xag_macaron",
        "strategy_name": "白银1小时低波动带量收复做多",
        "current": {
            "entry_price": entry,
            "stop_loss_price": stop,
            "stop_loss_pct": pct,
            "opened_at": "2026-08-22 06:01:00",
            "opened_at_ts": 1755813660,
            "strategy_key": "kimi_xag_macaron",
            "side": "long",
            "leverage": 7.56,
        },
    }


class HoldTribunalRulesTest(unittest.TestCase):
    def test_classify_or_not_and(self):
        self.assertIsNone(ht.classify_tier(0.28, 0.55))
        self.assertIsNone(ht.classify_tier(0.20, 0.60))
        self.assertEqual(ht.classify_tier(0.19, 0.50), "warn")
        self.assertEqual(ht.classify_tier(0.50, 0.61), "warn")
        self.assertEqual(ht.classify_tier(0.28, 0.72), "urgent")
        self.assertEqual(ht.classify_tier(0.11, 0.50), "urgent")
        self.assertEqual(ht.classify_tier(0.12, 0.69), "warn")
        self.assertEqual(ht.classify_tier(0.119, 0.50), "urgent")
        self.assertEqual(ht.classify_tier(0.50, 0.70), "urgent")

    def test_backup_cannot_escalate(self):
        self.assertEqual(ht.cap_backup_tier("urgent", "qwen"), "warn")
        self.assertEqual(ht.cap_backup_tier("warn", "qwen"), "warn")
        self.assertEqual(ht.cap_backup_tier("urgent", "deepseek"), "urgent")

    def test_stop_consumed_long(self):
        consumed = ht.stop_consumed(69.20, 68.33, 68.10, "long")
        self.assertAlmostEqual(consumed, (69.20 - 68.33) / (69.20 - 68.10), places=6)
        self.assertGreaterEqual(consumed, 0.70)

    def test_five_minute_shock(self):
        self.assertTrue(ht.five_minute_shock(
            "long", 68.10, {"open": 69.19, "low": 68.33, "high": 69.20}))
        self.assertFalse(ht.five_minute_shock(
            "long", 68.10, {"open": 69.10, "low": 69.00, "high": 69.12}))

    def test_ask_gate(self):
        self.assertTrue(ht.should_ask(0.79, False, None, 1000))
        self.assertFalse(ht.should_ask(0.40, False, None, 1000))
        self.assertTrue(ht.should_ask(0.40, True, None, 1000))
        self.assertFalse(ht.should_ask(0.79, True, 1000, 1000 + 1799))
        self.assertTrue(ht.should_ask(0.79, True, 1000, 1000 + 1800))

    def test_notify_only_each_half_hour(self):
        self.assertTrue(ht.should_notify("warn", None, None, 10))
        self.assertFalse(ht.should_notify("warn", "warn", 10, 10 + 10))
        self.assertFalse(ht.should_notify("urgent", "warn", 10, 11))
        self.assertTrue(ht.should_notify("warn", "warn", 10, 10 + 1800))
        self.assertFalse(ht.should_notify(None, "urgent", 10, 11))
        self.assertFalse(ht.should_notify(
            "urgent", "urgent", 10, 11, allow=False, last_slot=1, slot=2,
        ))
        self.assertFalse(ht.should_notify(
            "urgent", "urgent", 10, 11, allow=True, last_slot=4, slot=4,
        ))
        self.assertTrue(ht.should_notify(
            "urgent", "urgent", 10, 11, allow=True, last_slot=4, slot=5,
        ))

    def test_idle_poll_when_flat_active_when_holding(self):
        self.assertEqual(ht.next_poll_sec(0, poll_sec=30, idle_poll_sec=300), 300)
        self.assertEqual(ht.next_poll_sec(1, poll_sec=30, idle_poll_sec=300), 30)
        self.assertEqual(ht.next_poll_sec(2, poll_sec=30, idle_poll_sec=300), 30)
        # Unknown / error → keep short poll so we recover quickly.
        self.assertEqual(ht.next_poll_sec(-1, poll_sec=30, idle_poll_sec=300), 30)
        self.assertEqual(ht.live_position_count({"ok": True, "positions": []}), 0)
        self.assertEqual(ht.live_position_count({"ok": True, "positions": [{}, {}]}), 2)
        self.assertEqual(ht.live_position_count({"ok": False}), -1)

    def test_parse_vote_percent_and_alias(self):
        vote = ht.parse_vote({"p_take_profit": 28, "p_stop_68_10": 72, "reason_zh": "x"})
        self.assertTrue(vote["ok"])
        self.assertEqual(vote["p_take_profit"], 0.28)
        self.assertEqual(vote["p_stop"], 0.72)

    def test_message_uses_path_lexicon(self):
        msg = ht.format_warning_message({
            "tier": "urgent",
            "voter": "path_kernel",
            "p_take_profit": 0.28,
            "p_stop": 0.72,
            "reason_zh": "保护性止损12笔",
            "strategy_title": "白银1小时低波动带量收复做多（马卡龙）",
            "symbol_label": "XAG",
            "direction_zh": "做多",
            "entry_price": 69.20,
            "mark_price": 69.03,
            "stop_loss_price": 68.10,
            "stop_consumed": 0.79,
            "mae_pct": 0.0126,
        })
        self.assertIn("估算概率", msg)
        self.assertNotIn("自动交易辅助监测系统", msg)
        self.assertNotIn("庄园天气预报", msg)
        self.assertNotIn("持仓路径警告", msg)
        self.assertNotIn("持仓路径加急", msg)
        self.assertIn("止盈", msg)
        self.assertIn("止损", msg)
        self.assertIn("估算概率：止盈", msg)
        self.assertIn("逻辑推理", msg)
        self.assertNotIn("先盈", msg)
        self.assertNotIn("先亏", msg)
        self.assertNotIn("DeepSeek", msg)
        self.assertNotIn("状态向量核", msg)
        self.assertNotIn("未自动平仓", msg)
        self.assertNotIn("只建议复核", msg)

    def test_message_text_prefers_content_json_over_reasoning_prose(self):
        raw = {
            "choices": [{
                "message": {
                    "content": (
                        '{"p_take_profit":0.5,"p_stop":0.3,"reason_zh":"ok",'
                        '"evidence_hash":"h","cited_tools":["kernel"]}'
                    ),
                    "reasoning_content": "我们需要仔细演算很长一段没有大括号的中文推理……",
                }
            }]
        }
        text = ht._message_text(raw)
        self.assertTrue(text.startswith("{"))
        self.assertIn("p_take_profit", text)
        self.assertNotIn("我们需要仔细演算", text)

    def test_message_structure_voter(self):
        msg = ht.format_warning_message({
            "tier": "urgent",
            "voter": "path_kernel+structure",
            "p_take_profit": 0.10,
            "p_stop": 0.72,
            "reason_zh": "结构加急：已出更低高点",
            "strategy_title": "趋势回调买入优化V5",
            "symbol_label": "BTC",
            "direction_zh": "做多",
            "entry_price": 76900,
            "mark_price": 76864.9,
            "stop_loss_price": 75823.4,
        })
        self.assertIn("结构加急", msg)
        self.assertNotIn("DeepSeek", msg)
        msg2 = ht.format_warning_message({
            "tier": "urgent",
            "voter": "path_kernel+structure+deepseek",
            "p_take_profit": 0.10,
            "p_stop": 0.80,
            "reason_zh": "失败反抽后再次跌破开仓价",
            "strategy_title": "趋势回调买入优化V5",
            "symbol_label": "BTC",
            "direction_zh": "做多",
            "entry_price": 76900,
            "mark_price": 76864.9,
            "stop_loss_price": 75823.4,
        })
        self.assertIn("失败反抽", msg2)


class HoldTribunalTickTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._auto = Path(self._tmp)
        self._state = self._auto / "hold_tribunal_state.json"
        self._events = self._auto / "hold_tribunal_events.jsonl"
        self._patches = [
            mock.patch.object(ht, "AUTO_DIR", self._auto),
            mock.patch.object(ht, "STATE_PATH", self._state),
            mock.patch.object(ht, "EVENT_PATH", self._events),
        ]
        for p in self._patches:
            p.start()
        self._tf_patch = mock.patch(
            "auto_trade_hold_tf_samples.load_symbol_tf_bars",
            return_value={
                "ok": True,
                "candles": [
                    {"ts": 1, "open": 69.1, "high": 69.4, "low": 68.8, "close": 69.2},
                ] * 60,
                "n": 60,
                "atr_pct": 0.01,
                "error": None,
            },
        )
        self._tf_patch.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tf_patch.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, estimate_fn, notify_fn, listed=None, locals_row=None, candles=None, now_ts=1_000_000):
        listed = listed or {"ok": True, "positions": [_pos()]}
        locals_row = locals_row if locals_row is not None else _local()
        candles = candles if candles is not None else [
            {"ts": 1, "open": 69.19, "high": 69.25, "low": 68.33, "close": 69.03},
        ]
        with mock.patch("auto_trade_live_positions._collect_local_currents",
                        return_value=[locals_row]):
            return ht.evaluate_once(
                listed=listed,
                candles_fn=lambda inst: candles,
                estimate_fn=estimate_fn,
                notify_fn=notify_fn,
                now_ts=now_ts,
                dry_run=False,
            )

    def test_kernel_urgent_sends_once(self):
        sent = []
        calls = []

        def estimate(pos, snap):
            calls.append(snap.get("stop_consumed"))
            return {
                "ok": True, "voter": "path_kernel",
                "p_take_profit": 0.28, "p_stop": 0.72,
                "p_invalidation": 0.0, "p_timed": 0.0,
                "reason_zh": "止损邻居偏多",
            }

        def notify(message, kind, pack, dry_run):
            sent.append((kind, message, dry_run))
            return {"ok": True, "sent": True}

        out = self._run(estimate, notify)
        self.assertTrue(out["ok"])
        self.assertEqual(out["asked"], 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "hold_path_urgent")
        self.assertIn("止盈", sent[0][1])
        self.assertIn("估算概率", sent[0][1])
        self.assertNotIn("先亏", sent[0][1])

        sent.clear()
        out2 = self._run(estimate, notify)
        self.assertEqual(out2["asked"], 1)
        self.assertEqual(sent, [])
        self.assertEqual(len(calls), 2)

    def test_next_half_hour_slot_can_notify_again(self):
        sent = []

        def estimate(pos, snap):
            return {
                "ok": True, "author_ok": True, "voter": "remaining_path",
                "p_take_profit": 0.10, "p_stop": 0.80,
                "reason_zh": "加急",
            }

        def notify(message, kind, pack, dry_run):
            sent.append(kind)
            return {"ok": True, "sent": True}

        out = self._run(estimate, notify)
        self.assertEqual(sent, ["hold_path_urgent"])
        sent[:] = []
        out2 = self._run(estimate, notify, now_ts=1_000_000 + 1800)
        self.assertEqual(out2["asked"], 1)
        self.assertEqual(sent, ["hold_path_urgent"])

    def test_quiet_odds_do_not_notify(self):
        sent = []

        def estimate(pos, snap):
            return {
                "ok": True, "voter": "path_kernel",
                "p_take_profit": 0.429, "p_stop": 0.207,
                "p_invalidation": 0.262, "p_timed": 0.102,
                "reason_zh": "核估计安静",
            }

        candles = [{"open": 69.10, "high": 69.20, "low": 69.00, "close": 69.05}]
        local = _local(entry=69.20, stop=68.10)
        listed = {"ok": True, "positions": [_pos(mark_price=69.10)]}
        out = self._run(estimate, lambda *a, **k: sent.append(1) or {"sent": True},
                        listed=listed, locals_row=local, candles=candles)
        self.assertEqual(out["asked"], 1)
        self.assertEqual(sent, [])
        self.assertTrue(any(a.get("action") == "voted" and not a.get("notify") for a in out["actions"]))

    def test_default_path_uses_kernel_not_llm(self):
        fake = {
            "ok": True, "voter": "path_kernel",
            "p_take_profit": 0.43, "p_stop": 0.21,
            "reason_zh": "x",
        }
        with mock.patch.object(ht, "ask_provider", side_effect=AssertionError("llm")):
            with mock.patch.object(ht, "vote_hold_path", side_effect=AssertionError("llm")):
                with mock.patch.object(ht, "default_estimate", return_value=fake) as est:
                    with mock.patch("auto_trade_live_positions._collect_local_currents",
                                    return_value=[_local()]):
                        out = ht.evaluate_once(
                            listed={"ok": True, "positions": [_pos()]},
                            candles_fn=lambda inst: [
                                {"ts": 1, "open": 69.19, "high": 69.25, "low": 68.33, "close": 69.03},
                            ],
                            notify_fn=lambda *a, **k: {"sent": False},
                            now_ts=1_000_000,
                            dry_run=False,
                        )
        self.assertTrue(out["ok"])
        self.assertTrue(est.called)
        self.assertEqual(est.call_args[0][1].get("_now_ts"), 1_000_000)

    def test_manual_with_stop_is_estimated(self):
        sent = []
        seen = []

        def estimate(pos, snap):
            seen.append({
                "manual": pos.get("manual"),
                "title": snap.get("strategy_title"),
                "tf": snap.get("timeframe"),
                "stop": snap.get("stop_loss_price"),
                "opened": snap.get("opened_at_ts"),
            })
            return {
                "ok": True, "author_ok": True, "voter": "remaining_path",
                "p_take_profit": 0.0, "p_stop": 0.72, "p_timed": 0.28,
                "reason_zh": "手动仓保护止损",
            }

        def notify(message, kind, pack, dry_run):
            sent.append(kind)
            return {"ok": True, "sent": True}

        listed = {"ok": True, "positions": [_pos(
            manual=True, strategy_key=None, strategy_title="手动开仓",
            timeframe="", stop_loss_price=68.10, c_time="1700000000000",
        )]}
        out = self._run(estimate, notify, listed=listed, locals_row={})
        self.assertEqual(out["asked"], 1)
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0]["manual"])
        self.assertEqual(seen[0]["title"], "手动开仓")
        self.assertEqual(seen[0]["tf"], "1h")
        self.assertEqual(seen[0]["stop"], 68.10)
        self.assertEqual(seen[0]["opened"], 1700000000.0)

    def test_manual_without_stop_skips(self):
        sent = []

        def estimate(pos, snap):
            raise AssertionError("should not estimate without stop")

        def notify(*args, **kwargs):
            sent.append(1)
            return {"ok": True, "sent": True}

        listed = {"ok": True, "positions": [_pos(manual=True, strategy_key=None)]}
        with mock.patch.object(ht, "fetch_pending_algo_stops", return_value={"ok": True, "rows": []}):
            out = self._run(estimate, notify, listed=listed, locals_row={})
        self.assertEqual(out["asked"], 0)
        self.assertEqual(sent, [])
        self.assertTrue(any(a.get("action") == "skip_no_stop" and a.get("manual") for a in out["actions"]))

    def test_auto_missing_stop_still_skips(self):
        sent = []

        def estimate(pos, snap):
            raise AssertionError("should not estimate")

        def notify(*args, **kwargs):
            sent.append(1)
            return {"ok": True, "sent": True}

        listed2 = {"ok": True, "positions": [_pos()]}
        local = _local()
        local["current"]["stop_loss_price"] = None
        local["current"]["stop_loss_pct"] = None
        with mock.patch.object(ht, "fetch_pending_algo_stops", return_value={"ok": True, "rows": []}):
            out2 = self._run(estimate, notify, listed=listed2, locals_row=local, candles=[])
        self.assertEqual(out2["asked"], 0)
        self.assertTrue(any(a.get("action") == "skip_no_stop" for a in out2["actions"]))

    def test_exchange_algo_stop_fills_manual(self):
        snap = ht.build_snapshot(
            _pos(
                manual=True, strategy_key=None, strategy_title=None,
                stop_loss_price=None, c_time="1700000000000",
            ),
            {},
            [],
            {},
            now_ts=1700003600,
            algo_rows=[{
                "instId": "XAG-USDT-SWAP", "posSide": "long",
                "slTriggerPx": "68.05", "ordType": "conditional",
            }],
        )
        self.assertEqual(snap["stop_loss_price"], 68.05)
        self.assertEqual(snap["stop_source"], "exchange_algo")
        self.assertEqual(snap["strategy_title"], "手动开仓")
        self.assertEqual(snap["timeframe"], "1h")

    def test_quiet_consumed_still_estimates(self):
        sent = []

        def estimate(pos, snap):
            return {
                "ok": True, "voter": "path_kernel",
                "p_take_profit": 0.429, "p_stop": 0.207,
                "reason_zh": "浅回撤",
            }

        candles = [{"open": 69.10, "high": 69.20, "low": 69.00, "close": 69.05}]
        local = _local(entry=69.20, stop=68.10)
        listed = {"ok": True, "positions": [_pos(mark_price=69.10)]}
        out = self._run(estimate, lambda *a, **k: sent.append(1) or {"sent": True},
                        listed=listed, locals_row=local, candles=candles)
        self.assertEqual(out["asked"], 1)
        self.assertEqual(sent, [])
        self.assertTrue(any(a.get("action") == "voted" and not a.get("notify") for a in out["actions"]))

    def test_failed_wx_does_not_start_cooldown(self):
        sent = []

        def estimate(pos, snap):
            return {
                "ok": True, "voter": "path_kernel",
                "p_take_profit": 0.10, "p_stop": 0.72,
                "reason_zh": "加急",
            }

        def notify(message, kind, pack, dry_run):
            sent.append(kind)
            return {"ok": False, "sent": False}

        out = self._run(estimate, notify)
        self.assertEqual(sent, ["hold_path_urgent"])
        self.assertTrue(any(a.get("notify") and not a.get("sent") for a in out["actions"]))
        sent[:] = []
        out2 = self._run(estimate, notify)
        self.assertEqual(out2["asked"], 1)
        self.assertEqual(sent, ["hold_path_urgent"])


class HoldWxKindTest(unittest.TestCase):
    def test_kinds_go_to_hold_assist(self):
        self.assertEqual(notify._wx_summary("hold_path_warning"), "cosmic观风金球")
        self.assertEqual(notify._wx_summary("hold_path_urgent"), "cosmic观风金球")
        self.assertEqual(ht.notify_kind("urgent"), "hold_path_urgent")
        self.assertEqual(ht.notify_kind("warn"), "hold_path_warning")


class HoldAssistBoardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._auto = Path(self._tmp)
        self._state = self._auto / "hold_tribunal_state.json"
        self._events = self._auto / "hold_tribunal_events.jsonl"
        self._patches = [
            mock.patch.object(ht, "AUTO_DIR", self._auto),
            mock.patch.object(ht, "STATE_PATH", self._state),
            mock.patch.object(ht, "EVENT_PATH", self._events),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_board_keeps_open_warn_and_drops_closed(self):
        live = _pos()
        live_key = ht._position_state_key(live)
        gone_key = "GONE-USDT-SWAP|long|isolated"
        ht._write_json(ht.STATE_PATH, {
            "positions": {
                live_key: {
                    "last_notify_tier": "warn",
                    "last_p_take_profit": 0.18,
                    "last_p_stop": 0.62,
                    "last_notify_at": "2026-08-23 12:00:00",
                    "last_notify_ts": 1000,
                    "last_reason_zh": "止损概率升高",
                    "last_strategy_title": live["strategy_title"],
                },
                gone_key: {
                    "last_notify_tier": "urgent",
                    "last_p_take_profit": 0.10,
                    "last_p_stop": 0.80,
                    "last_notify_ts": 2000,
                },
            }
        })
        board = ht.trigger_board(
            listed={"ok": True, "positions": [live]},
            persist=True,
        )
        self.assertTrue(board["ok"])
        self.assertEqual(board["count"], 1)
        self.assertEqual(board["triggers"][0]["position_id"], live_key)
        self.assertEqual(board["triggers"][0]["tier_zh"], "警告")
        self.assertEqual(len(board["positions"]), 1)
        self.assertEqual(board["positions"][0]["status"], "warn")
        self.assertIn(gone_key, board["pruned"])
        saved = ht._load_state()
        self.assertNotIn(gone_key, saved.get("positions") or {})
        self.assertIn(live_key, saved.get("positions") or {})

    def test_board_skips_prune_when_positions_unavailable(self):
        ht._write_json(ht.STATE_PATH, {
            "positions": {
                "KEEP|long|isolated": {"last_notify_tier": "urgent"},
            }
        })
        board = ht.trigger_board(
            listed={"ok": False, "error": "down"},
            persist=True,
        )
        self.assertFalse(board["ok"])
        self.assertEqual(board["triggers"], [])
        saved = ht._load_state()
        self.assertIn("KEEP|long|isolated", saved.get("positions") or {})

    def test_board_skips_prune_while_positions_building(self):
        ht._write_json(ht.STATE_PATH, {
            "positions": {
                "KEEP|long|isolated": {"last_notify_tier": "warn"},
            }
        })
        board = ht.trigger_board(
            listed={"ok": True, "building": True, "positions": []},
            persist=True,
        )
        self.assertFalse(board["ok"])
        saved = ht._load_state()
        self.assertIn("KEEP|long|isolated", saved.get("positions") or {})

    def test_board_lists_watch_positions_without_wx(self):
        live = _pos()
        live_key = ht._position_state_key(live)
        ht._write_json(ht.STATE_PATH, {
            "positions": {
                live_key: {
                    "last_tier": None,
                    "last_p_take_profit": 0.28,
                    "last_p_stop": 0.52,
                    "last_p_timed": 0.20,
                    "last_reason_zh": "动量极弱但尚未回吐",
                    "last_guidance_zh": "建议继续交给本策略退出计划。",
                    "last_guidance_action": "hold_to_plan",
                    "last_guidance_ok": True,
                    "last_voter": "deepseek-v4",
                    "last_formulas": {"weakening_intensity": 0.0, "giveback_frac": None},
                    "last_estimate_at": "2026-08-25 19:22:01",
                },
            }
        })
        board = ht.trigger_board(
            listed={"ok": True, "positions": [live]},
            persist=False,
        )
        self.assertTrue(board["ok"])
        self.assertEqual(board["count"], 1)
        self.assertEqual(board["alert_count"], 0)
        self.assertEqual(board["triggers"], [])
        row = board["positions"][0]
        self.assertEqual(row["status"], "watch")
        self.assertEqual(row["status_zh"], "观察")
        self.assertEqual(row["p_stop"], 0.52)
        self.assertEqual(row["p_timed"], 0.20)
        self.assertEqual(row["voter_zh"], "DeepSeek-v4")
        self.assertIn("动量极弱", row["reason_zh"])
        self.assertIn("建议继续交给本策略退出计划", row["guidance_zh"])
        self.assertEqual(row["guidance_action"], "hold_to_plan")


if __name__ == "__main__":
    unittest.main()
