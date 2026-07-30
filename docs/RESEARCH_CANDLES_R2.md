# 研究长历史 K 线 — Cloudflare R2 / S3（方案 B）

实盘 `formal_*_candles_cache.json` 仍是短滚动窗（5m≈1400 根），**不要改大**。  
创造/回测的长历史走独立对象存储。

## 架构

```
OKX history-candles
        │
        ▼  scripts/research_candles_backfill_r2.py
Cloudflare R2 / S3
  okx/candles/{instId}/{bar}/v1/manifest.json
  okx/candles/{instId}/{bar}/v1/chunks/{YYYYMM}.json.gz
        │
        ▼  创造时按需拉取
本地磁盘缓存 /root/auto_trade/research_candle_cache/
        │
        ▼
creation_blueprint / easyquant_bridge.load_candles
```

## 环境变量（写入 `/root/auto_trade/ai_ecosystem.env`）

```bash
# Cloudflare R2
QIYU_R2_ACCOUNT_ID=xxxxxxxx
QIYU_R2_ACCESS_KEY_ID=xxxxxxxx
QIYU_R2_SECRET_ACCESS_KEY=xxxxxxxx
QIYU_R2_BUCKET=qiyu-research-candles
# 可选：显式 endpoint（默认 https://{ACCOUNT_ID}.r2.cloudflarestorage.com）
# QIYU_R2_ENDPOINT=https://xxxxxxxx.r2.cloudflarestorage.com

# 模式：auto（有密钥用 R2，否则 local）| r2 | s3 | local | off
QIYU_RESEARCH_CANDLES_MODE=auto

# 创造偏好研究仓
QIYU_CREATION_PREFER_RESEARCH=1
QIYU_RESEARCH_LOOKBACK_DAYS=400
QIYU_CREATION_MAX_BARS=20000
```

通用 S3 也可用：`QIYU_S3_ENDPOINT` / `QIYU_S3_ACCESS_KEY_ID` / `QIYU_S3_SECRET_ACCESS_KEY` / `QIYU_S3_BUCKET`。

无云端密钥时可用本地镜像过渡：

```bash
QIYU_RESEARCH_CANDLES_MODE=local
# 数据目录默认 /root/auto_trade/research_candle_store
```

## 回填

```bash
# 建议先 ADA 5m 自 2024-01-01（内存友好：按月 gzip chunk）
python3 scripts/research_candles_backfill_r2.py \
  --symbol ADA-USDT-SWAP --bar 5m --since 2024-01-01

python3 scripts/research_candles_backfill_r2.py \
  --symbol BTC-USDT-SWAP --bar 15m --since 2024-01-01
```

## 验证

```bash
python3 - <<'PY'
from dual_engine_workflow_v2 import research_candle_store as rcs
print(rcs.probe())
print(rcs.load_for_creation("ADA-USDT-SWAP","5m", lookback_days=30).get("n"))
PY
```

## 铁律

- 不改 formal 短窗上限，不与实盘热缓存混写。
- 不自动挂载；创造仍走 ADA5 复核。
- VPS 仅缓存当前需要的月份，避免一次载入全年进内存。
