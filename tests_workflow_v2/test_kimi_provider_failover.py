# -*- coding: utf-8 -*-
from __future__ import print_function

import io
import json
import os
import sys
import unittest
from unittest import mock
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dual_engine_workflow_v2 import kimi_provider as kp


def _http_error(code, reason, body):
    exc = urllib.error.HTTPError(
        "https://cmkey.cn/v1/chat/completions", code, reason, hdrs=None, fp=io.BytesIO(body.encode("utf-8")),
    )
    return exc


def _ok_raw(text='{"ok":true}'):
    return {
        "id": "req_backup",
        "choices": [{"message": {"content": text}}],
        "usage": {},
    }


class _Resp(object):
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestKimiFailover(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {
            "QIYU_KIMI_URL": "https://cmkey.cn/v1",
            "QIYU_KIMI_API_KEY": "primary-key",
            "QIYU_KIMI_MODEL": "kimi-k3",
            "QIYU_KIMI_BACKUP_URL": "https://api2.cmkey.cn/v1",
            "QIYU_KIMI_BACKUP_API_KEY": "backup-key",
            "QIYU_KIMI_BACKUP_MODEL": "kimi-k3",
        }, clear=False)
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_invent_chain_includes_qwen_deepseek(self):
        with mock.patch.dict(os.environ, {
            "QIYU_QWEN_API_KEY": "qwen-key",
            "QIYU_QWEN_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "QIYU_QWEN_MODEL": "qwen3.7-plus",
            "QIYU_DEEPSEEK_API_KEY": "ds-key",
            "QIYU_DEEPSEEK_URL": "https://api.deepseek.com/chat/completions",
            "QIYU_DEEPSEEK_MODEL": "deepseek-v4-pro",
        }, clear=False):
            names = [r["name"] for r in kp.invent_endpoint_chain()]
        self.assertIn("primary", names)
        self.assertIn("backup", names)
        self.assertIn("qwen", names)
        self.assertIn("deepseek", names)

    def test_named_429_fails_over_to_qwen(self):
        calls = []

        def fake_open(req, timeout):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            calls.append(url)
            if "dashscope" in url:
                return _Resp(_ok_raw('{"recipe":{"ok":true}}'))
            raise _http_error(
                429, "Too Many Requests",
                '{"error":{"message":"inference tpm exhausted","code":"429001"}}',
            )

        with mock.patch.dict(os.environ, {
            "QIYU_QWEN_API_KEY": "qwen-key",
            "QIYU_QWEN_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "QIYU_DEEPSEEK_API_KEY": "",
        }, clear=False):
            with mock.patch.object(kp, "_open_no_proxy", side_effect=fake_open):
                posted = kp.kimi_post_named("primary", {"messages": []}, timeout=5)
        self.assertTrue(posted.get("ok"))
        self.assertEqual(posted.get("endpoint"), "qwen")
        self.assertTrue(posted.get("failover"))

    def test_named_429_fails_over_to_other_endpoint(self):
        calls = []

        def fake_open(req, timeout):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            calls.append(url)
            if "api2.cmkey.cn" in url:
                raise _http_error(
                    429, "Too Many Requests",
                    '{"error":{"message":"inference tpm exhausted","code":"429001"}}',
                )
            return _Resp(_ok_raw('{"recipe":{"ok":true}}'))

        with mock.patch.object(kp, "_open_no_proxy", side_effect=fake_open):
            posted = kp.kimi_post_named("backup", {"messages": []}, timeout=5)
        self.assertTrue(posted.get("ok"))
        self.assertEqual(posted.get("endpoint"), "primary")
        self.assertTrue(posted.get("failover"))
        self.assertEqual(posted.get("failover_from"), "backup")
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("api2.cmkey.cn" in u for u in calls))
        self.assertTrue(any("api2.cmkey.cn" not in u for u in calls))

    def test_named_non_failover_stays_on_preferred(self):
        calls = []

        def fake_open(req, timeout):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            calls.append(url)
            raise _http_error(
                400, "Bad Request",
                '{"error":{"message":"bad schema"}}',
            )

        with mock.patch.object(kp, "_open_no_proxy", side_effect=fake_open):
            posted = kp.kimi_post_named("backup", {"messages": []}, timeout=5)
        self.assertFalse(posted.get("ok"))
        self.assertEqual(posted.get("endpoint"), "backup")
        self.assertFalse(posted.get("failover"))
        self.assertEqual(len(calls), 1)

    def test_http_500_fails_over_to_backup(self):
        calls = []

        def fake_open(req, timeout):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            calls.append(url)
            if "api2.cmkey.cn" in url:
                return _Resp(_ok_raw())
            raise _http_error(
                500, "Internal Server Error",
                '{"error":{"message":"上游服务器内部错误","type":"bad_response_body"}}',
            )

        with mock.patch.object(kp, "_open_no_proxy", side_effect=fake_open):
            posted = kp.kimi_post_json({"messages": []}, timeout=5)
        self.assertTrue(posted.get("ok"))
        self.assertEqual(posted.get("endpoint"), "backup")
        self.assertEqual(len(calls), 2)

    def test_401_fails_over_to_backup(self):
        def fake_open(req, timeout):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            if "api2.cmkey.cn" in url:
                return _Resp(_ok_raw())
            raise _http_error(401, "Unauthorized", '{"error":{"message":"身份验证失败：令牌无效或已过期"}}')

        with mock.patch.object(kp, "_open_no_proxy", side_effect=fake_open):
            posted = kp.kimi_post_json({"messages": []}, timeout=5)
        self.assertTrue(posted.get("ok"))
        self.assertEqual(posted.get("endpoint"), "backup")

    def test_empty_primary_content_fails_over(self):
        def fake_open(req, timeout):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            if "api2.cmkey.cn" in url:
                return _Resp(_ok_raw())
            return _Resp({"choices": [{"message": {"content": ""}}]})

        with mock.patch.object(kp, "_open_no_proxy", side_effect=fake_open):
            posted = kp.kimi_post_json({"messages": []}, timeout=5)
        self.assertTrue(posted.get("ok"))
        self.assertEqual(posted.get("endpoint"), "backup")

    def test_500_is_failover_class(self):
        self.assertTrue(kp.kimi_should_failover(
            'HTTPError:HTTP Error 500: Internal Server Error {"type":"bad_response_body"}'
        ))

    def test_http_504_retries_same_endpoint_then_succeeds(self):
        calls = []

        def fake_open(req, timeout):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            calls.append(url)
            if len(calls) == 1:
                raise _http_error(
                    504, "Gateway Time-out",
                    "<html>504 Gateway Time-out nginx/1.24.0</html>",
                )
            return _Resp(_ok_raw('{"ok":true,"from":"primary"}'))

        lone = {
            "QIYU_KIMI_BACKUP_URL": "",
            "QIYU_KIMI_BACKUP_API_KEY": "",
            "QIYU_KIMI_URL_2": "",
            "QIYU_KIMI_API_KEY_2": "",
        }
        with mock.patch.dict(os.environ, lone, clear=False):
            with mock.patch.object(kp, "_open_no_proxy", side_effect=fake_open):
                with mock.patch.object(kp.time, "sleep"):
                    posted = kp.kimi_post_json({"messages": []}, timeout=5)
        self.assertTrue(posted.get("ok"))
        self.assertEqual(posted.get("endpoint"), "primary")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("cmkey.cn" in url and "api2" not in url for url in calls))

    def test_http_504_races_backup_without_same_endpoint_retry(self):
        calls = []

        def fake_open(req, timeout):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            calls.append(url)
            if "api2.cmkey.cn" in url:
                return _Resp(_ok_raw())
            raise _http_error(
                504, "Gateway Time-out",
                "<html>504 Gateway Time-out nginx/1.24.0</html>",
            )

        with mock.patch.object(kp, "_open_no_proxy", side_effect=fake_open):
            with mock.patch.object(kp.time, "sleep"):
                posted = kp.kimi_post_json({"messages": []}, timeout=5)
        self.assertTrue(posted.get("ok"))
        self.assertEqual(posted.get("endpoint"), "backup")
        self.assertTrue(posted.get("raced"))
        backup_hits = [url for url in calls if "api2.cmkey.cn" in url]
        primary_hits = [url for url in calls if "api2.cmkey.cn" not in url]
        self.assertEqual(len(backup_hits), 1)
        self.assertEqual(len(primary_hits), 1)

    def test_slow_primary_does_not_block_backup(self):
        import time

        def fake_open(req, timeout):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            if "api2.cmkey.cn" in url:
                return _Resp(_ok_raw())
            time.sleep(1.5)
            return _Resp(_ok_raw())

        t0 = time.time()
        with mock.patch.object(kp, "_open_no_proxy", side_effect=fake_open):
            posted = kp.kimi_post_json({"messages": []}, timeout=5)
        self.assertTrue(posted.get("ok"))
        self.assertEqual(posted.get("endpoint"), "backup")
        self.assertLess(time.time() - t0, 1.0)

    def test_timeout_is_failover_class(self):
        self.assertTrue(kp.kimi_should_failover("TimeoutError: timed out"))
        self.assertTrue(kp.kimi_should_failover(
            "socket.timeout:kimi_wall_clock_timeout:540s"
        ))

    def test_wall_clock_aborts_hung_open(self):
        import socket
        import time

        def hang(req, timeout):
            time.sleep(2)
            return _Resp(_ok_raw())

        with mock.patch.object(kp, "_open_no_proxy", side_effect=hang):
            with mock.patch.object(kp, "WALL_CLOCK_GRACE_SEC", 0):
                with self.assertRaises(socket.timeout) as ctx:
                    kp._open_with_deadline(mock.Mock(), timeout=0.2)
        self.assertIn("kimi_wall_clock_timeout", str(ctx.exception))

    def test_hung_primary_fails_over_to_backup(self):
        import time

        def fake_open(req, timeout):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            if "api2.cmkey.cn" in url:
                return _Resp(_ok_raw())
            time.sleep(2)
            return _Resp(_ok_raw())

        with mock.patch.object(kp, "_open_no_proxy", side_effect=fake_open):
            with mock.patch.object(kp, "WALL_CLOCK_GRACE_SEC", 0):
                posted = kp.kimi_post_json({"messages": []}, timeout=0.2)
        self.assertTrue(posted.get("ok"))
        self.assertEqual(posted.get("endpoint"), "backup")


class TestTransportBackoff(unittest.TestCase):
    def test_gateway_errors_use_short_backoff(self):
        self.assertEqual(5, kp.kimi_transport_backoff_seconds("HTTP 504 Gateway Time-out", 0))
        self.assertEqual(10, kp.kimi_transport_backoff_seconds("HTTP Error 502: Bad Gateway", 1))
        self.assertEqual(30, kp.kimi_transport_backoff_seconds("timed out", 4))
        self.assertEqual(60, kp.kimi_transport_backoff_seconds("HTTP 500 Internal Server Error", 0))


class TestEndpointQuotaSoftBlock(unittest.TestCase):
    def setUp(self):
        kp.reset_endpoint_quota_for_tests()
        self.env = mock.patch.dict(os.environ, {
            "QIYU_KIMI_URL": "https://cmkey.cn/v1",
            "QIYU_KIMI_API_KEY": "primary-key",
            "QIYU_KIMI_MODEL": "kimi-k3",
            "QIYU_KIMI_BACKUP_URL": "https://api2.cmkey.cn/v1",
            "QIYU_KIMI_BACKUP_API_KEY": "backup-key",
            "QIYU_DEEPSEEK_API_KEY": "ds-key",
            "QIYU_DEEPSEEK_URL": "https://api.deepseek.com/chat/completions",
            "KDH_ENDPOINT_LIMIT_deepseek": "5",
            "KDH_ENDPOINT_QUOTA_SOFT_RATIO": "0.8",
            "KDH_ENDPOINT_QUOTA_PATH": "/tmp/kdh_endpoint_quota_test.json",
        }, clear=False)
        self.env.start()
        try:
            os.unlink("/tmp/kdh_endpoint_quota_test.json")
        except Exception:
            pass
        kp.reset_endpoint_quota_for_tests()

    def tearDown(self):
        self.env.stop()
        kp.reset_endpoint_quota_for_tests()

    def test_soft_block_skips_deepseek_failover(self):
        for _ in range(4):
            kp.record_endpoint_attempt("deepseek")
        blocked, count, limit = kp.endpoint_soft_blocked("deepseek")
        self.assertTrue(blocked)
        self.assertEqual(limit, 5)
        self.assertGreaterEqual(count, 4)

        deepseek_calls = []

        def fake_open(req, timeout):
            url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
            if "api.deepseek.com" in url:
                deepseek_calls.append(url)
                raise AssertionError("deepseek should be soft-blocked")
            if "api2.cmkey.cn" in url:
                return _Resp(_ok_raw('{"recipe":{"ok":true}}'))
            raise _http_error(
                429, "Too Many Requests",
                '{"error":{"message":"tpm","code":"429001"}}',
            )

        with mock.patch.dict(os.environ, {"QIYU_QWEN_API_KEY": ""}, clear=False):
            with mock.patch.object(kp, "_open_no_proxy", side_effect=fake_open):
                posted = kp.kimi_post_named("primary", {"messages": []}, timeout=5)
        self.assertTrue(posted.get("ok"))
        self.assertEqual(posted.get("endpoint"), "backup")
        self.assertEqual(deepseek_calls, [])
        # Direct soft-block response when preferred mouth is exhausted
        ds = {
            "name": "deepseek",
            "url": "https://api.deepseek.com/v1/chat/completions",
            "key": "ds-key",
            "model": "deepseek-v4-pro",
        }
        blocked_post = kp._kimi_post_one(ds, {"messages": []}, 5, 0)
        self.assertFalse(blocked_post.get("ok"))
        self.assertTrue(blocked_post.get("soft_block"))
        self.assertIn("rate_limit_soft_block", str(blocked_post.get("error") or ""))

    def test_json_parse_critique_mentions_schema(self):
        msg = kp.json_parse_critique("not json at all")
        self.assertIn("JSON 校验失败", msg)
        self.assertIn("recipe", msg)


if __name__ == "__main__":
    unittest.main()
