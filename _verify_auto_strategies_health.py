#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import print_function

import json
import os
import sys
import urllib.request

sys.path.insert(0, "/root")


def main():
    # locate web_server port
    ports = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = open("/proc/%s/cmdline" % pid, "rb").read().replace(b"\0", b" ").decode()
        except Exception:
            continue
        if "web_server.py" in cmd:
            print("PROC", pid, cmd[:240])
    for port in (80, 443, 5000, 5001, 8080, 8888, 3000, 8000, 5173, 8765, 9000):
        for path in (
            "/api/auto_trade_health_summary",
            "/api/auto_trade_health",
            "/api/auto_trade_status",
            "/api/system_status",
        ):
            url = "http://127.0.0.1:%d%s" % (port, path)
            try:
                raw = urllib.request.urlopen(url, timeout=2).read()
                print("HIT", url, raw[:180])
                d = json.loads(raw)

                def walk(o):
                    if isinstance(o, dict):
                        if o.get("id") == "auto_strategies" or o.get("name") == "自动交易策略监测":
                            print(
                                "STATUS",
                                o.get("status") or o.get("state"),
                                "summary=",
                                str(o.get("summary") or o.get("detail") or "")[:160],
                            )
                        for v in o.values():
                            walk(v)
                    elif isinstance(o, list):
                        for v in o:
                            walk(v)

                walk(d)
                if isinstance(d, dict):
                    print(
                        "overall",
                        d.get("summary") or d.get("overall") or d.get("status"),
                        "healthy",
                        d.get("healthy_count"),
                        "/",
                        d.get("total_count"),
                    )
                return
            except Exception:
                continue
    # fallback: call builder directly
    import web_server as ws

    for name in dir(ws):
        obj = getattr(ws, name)
        if callable(obj) and "health" in name.lower() and "summary" in name.lower():
            print("try", name)
            try:
                out = obj()
                print(type(out), str(out)[:300])
            except TypeError:
                pass
            except Exception as e:
                print("err", name, e)


if __name__ == "__main__":
    main()
