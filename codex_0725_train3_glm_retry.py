# -*- coding: utf-8 -*-
"""Retry GLM-5.2 propose for train3; write glm_proposals_retry.json."""
from __future__ import print_function
import json
import os
import time
from pathlib import Path

OUT = Path("/root/auto_trade/codex_0725_train3")
OUT.mkdir(parents=True, exist_ok=True)

for line in Path("/root/auto_trade/ai_ecosystem.env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

import auto_trade_ai_consensus as ai
import urllib.request as ur

cfg = ai._provider_config("glm")
prompt = (
    "只输出JSON对象。根据ADA顺势回升模板，给出8个新DSL变体。"
    "硬约束: schema=qiyu_strategy_dsl_v1; key以codex0725t3_开头; name含·0725T3;"
    "特征仅限 h1_ema19,h1_ema53,h1_slope4,rsi14,close,open,ema21,z20,k,prev_low20,prev_high20;"
    "exit可有role take_profit/invalidation。标的可选 ETH/SOL/BNB/XAG/LTC/ADA/XAU。"
    "勿复刻已上线 r42_z2p0_h14 与 r42_z2p2_h14。"
    '格式: {"strategies":[...],"notes":"..."}'
)
body = {
    "model": cfg["model"],
    "messages": [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(
            {"template": "ada5 trend pullback long", "n": 8},
            ensure_ascii=False)},
    ],
    "temperature": 0.2,
    "max_tokens": 6000,
}
req = ur.Request(
    cfg["url"], data=json.dumps(body).encode("utf-8"),
    headers={"Authorization": "Bearer " + cfg["api_key"],
             "Content-Type": "application/json"}, method="POST")
t0 = time.time()
with ur.urlopen(req, timeout=180) as resp:
    raw = json.loads(resp.read().decode("utf-8"))
msg = (raw.get("choices") or [{}])[0].get("message") or {}
content = msg.get("content") or msg.get("reasoning_content") or ""
print("latency", round(time.time() - t0, 2), "content_len", len(content), flush=True)
(OUT / "glm_raw.txt").write_text(content or "", encoding="utf-8")
text = str(content or "")
parsed = None
try:
    parsed = ai._parse_content_json(text)
except Exception:
    s = text.find("{")
    e = text.rfind("}")
    if s >= 0 and e > s:
        try:
            parsed = json.loads(text[s:e + 1])
        except Exception as ex:
            print("parse_fail", ex, flush=True)
print("parsed", type(parsed).__name__, flush=True)
if isinstance(parsed, dict):
    n = len(parsed.get("strategies") or [])
    print("n_strategies", n, flush=True)
    (OUT / "glm_proposals_retry.json").write_text(
        json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT / "glm_proposals_retry.json", flush=True)
