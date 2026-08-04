# 量化系统全景架构与多 AI 协同机制对接报告

> **生成时间**: 2026-07-29 (UTC+8)  
> **证据来源**: 本地 workspace `intraday_live_v1_20260720` + 生产 SSH 快照 `root@64.176.47.192`  
> **原则**: 仅列真实路径/片段；未知项标 **UNVERIFIED**；不含密钥/Token

---

## 目录 (TOC)

1. [拓扑与服务 (Topology & Services)](#1-拓扑与服务-topology--services)
2. [多 AI 协同 (Multi-AI Collaboration)](#2-多-ai-协同-multi-ai-collaboration)
3. [策略生命周期与引擎 (Strategy Lifecycle & Engine)](#3-策略生命周期与引擎-strategy-lifecycle--engine)
4. [数据管道 (Data Pipeline)](#4-数据管道-data-pipeline)
5. [执行与遥测 (Execution & Telemetry)](#5-执行与遥测-execution--telemetry)
6. [Web API 与控制台 (Web APIs & Console)](#6-web-api-与控制台-web-apis--console)
7. [Gemini 插件对接指南 (Gemini Plug-in Guide)](#7-gemini-插件对接指南-gemini-plug-in-guide)

---

## 1. 拓扑与服务 (Topology & Services)

### 1.1 生产主机概览

| 项 | 值 |
|---|---|
| 主机 | `64.176.47.192` (Vultr) |
| 根目录 | `/root` (`VECTOR_ROOT`) |
| Web 控制台 | `0.0.0.0:8080` → `/root/web_server.py` (pid 1196) |
| Redis | `127.0.0.1:6379` |
| SSH | port 22 |

### 1.2 运行中进程 (2026-07-29 13:04 CST 快照)

| 进程 | 数量/说明 |
|---|---|
| `auto_trade_formal_daemon.run_forever()` | **13** 个实例 (不同 symbol/timeframe) |
| `/usr/bin/python3 /root/web_server.py` | 1 (Flask, port 8080) |
| `auto_trade_execution_cost_calibrator.py` | 1 (flock 单实例, qiyu-execution-cost-calibrator) |

Formal daemon 启动形态（生产实测）:

```python
/usr/bin/python3 -c import auto_trade_formal_daemon as d; d.run_forever()
```

每个实例通过环境变量区分标的/周期，配置文件后缀见 `auto_trade_formal_daemon.py`:

```python
CONFIG_FILE = AUTO_DIR / ("formal_daemon_config%s.json" % INSTANCE_SUFFIX)
# 例: formal_daemon_config_ada_5m.json
```

### 1.3 关键目录树

```
/root/
├── web_server.py                    # Flask 控制台 + API 网关
├── backtest_engine_v2.py            # 回测/Parquet 加载
├── auto_trade/                      # 运行时状态、配置、审计
│   ├── auto_trade_config.json
│   ├── ai_ecosystem.env             # QIYU_* API 密钥 (600 权限, 不含于本报告)
│   ├── ai_research_consent.json     # 外部 AI 调用同意书
│   ├── dual_engine/                 # 双引擎工作流产物
│   │   ├── status.json
│   │   ├── insight_latest.json
│   │   ├── formal_submits.json
│   │   └── workflow_v2/artifacts/   # STEP A 工件
│   ├── formal_daemon_config*.json   # 各实例 daemon 配置
│   └── system_forecast_latest.json
├── dual_engine_workflow_v2/         # 策略工作流 v2 源码 (生产副本)
├── strategy_configs/
│   ├── experimental_strategies.json
│   └── ai_dsl_strategies.json
├── market_data/                     # Parquet K 线 (见 §4)
├── docs/                            # 归档报告与审计 JSON
└── logs/backtest/formal_daemon*.log
```

本地 workspace 镜像同构，额外含 `systemd/` 单元文件、`windtalker_phase*/` 研究部署包。

### 1.4 Systemd 定时/服务 (本地 `systemd/` 定义)

| Unit | 用途 |
|---|---|
| `qiyu-system-forecast.service` + `.timer` | 周一 UTC+8 08:00 三 AI 系统预测 |
| `qiyu-human-confirm-pipeline.service` + `.timer` | 人工确认流水线 tick |
| `qiyu-strategy-creation-factory.service` + `.timer` | 三 AI 策略创造工厂 |
| `qiyu-strategy-lifecycle.service` + `.timer` | 策略生命周期治理 |
| `qiyu-strategy-dynamic-optimizer.service` | 动态优化 |
| `qiyu-mass-engine.service` | 量产引擎 (E/D 探针已冻结) |
| `qiyu-adaptive-engine.service` | 自适应引擎 tick |
| `qiyu-microstructure-primitives.service` | 微观结构基元采集 |
| `qiyu-shadow-validator.service` | 影子验证 |
| `qiyu-formal-auto-trade-ada-5m.service` | ADA 5m 正式 daemon (生产 active) |
| `qiyu-execution-cost-calibrator.service` | 执行成本遥测校准 |

生产 `systemctl` 快照显示 `qiyu-formal-auto-trade-ada-5m.service` **active running**，`qiyu-execution-cost-calibrator.service` 正在 activating。

### 1.5 网络拓扑 (ASCII)

```
                    ┌─────────────────────────────────────┐
                    │  Browser / WxPusher / OKX API       │
                    └──────────┬──────────────┬───────────┘
                               │ :8080        │ HTTPS
                    ┌──────────▼──────────┐   │
                    │  web_server.py      │   │
                    │  Flask + BasicAuth  │   │
                    └──────────┬──────────┘   │
           ┌───────────────────┼────────────┼──────────────┐
           │                   │            │              │
    ┌──────▼──────┐   ┌───────▼──────┐  ┌──▼───┐   ┌─────▼─────┐
    │ formal_daemon│×N │ dual_engine  │  │Redis │   │ OKX REST  │
    │ (per symbol) │   │ workflow_v2  │  │:6379 │   │ (live)    │
    └──────┬──────┘   └───────┬──────┘  └──────┘   └───────────┘
           │                  │
    ┌──────▼──────────────────▼──────┐
    │  /root/auto_trade/  (JSON/JSONL)│
    │  /root/market_data/  (Parquet)  │
    └─────────────────────────────────┘
```

---

## 2. 多 AI 协同 (Multi-AI Collaboration)

### 2.1 提供商矩阵

| Provider | 环境变量前缀 | 默认 Endpoint | 默认 Model |
|---|---|---|---|
| DeepSeek | `QIYU_DEEPSEEK_*` | `https://api.deepseek.com/chat/completions` | `deepseek-v4-pro` |
| Qwen (通义) | `QIYU_QWEN_*` | DashScope compatible-mode | `qwen3.7-plus` |
| GLM (智谱) | `QIYU_GLM_*` | `https://open.bigmodel.cn/api/paas/v4/chat/completions` | `glm-5.2` |

> 历史别名 `chatgpt`/`openai`/`gpt` 在 `_normalize_provider_name()` 中映射到 `glm`。

核心模块: `/root/auto_trade_ai_consensus.py` (生产) / 本地同名文件。

```python
PROVIDERS = ("deepseek", "qwen", "glm")
CONSENT_SCOPES = ("redacted_market_research_data", "redacted_trade_research_data")
```

### 2.2 同意书与密钥隔离

所有外部 AI 网络调用**必须先过** `external_research_consent_status()`:

- 同意书: `/root/auto_trade/ai_research_consent.json`
- 密钥: `/root/auto_trade/ai_ecosystem.env` (仅 root 可读, **本报告不引用值**)
- 缺失/拒绝 consent → **fail-closed**, 即使 API key 存在

`auto_trade_ai_consensus.py` 模块注释明确: **无交易所凭证, 不能执行交易**。

### 2.3 协同模式一览

| 模式 | 模块 | AI 角色 | 决策规则 |
|---|---|---|---|
| **三 AI 一致复核** | `auto_trade_ai_consensus.unanimous_review()` | DS + Qwen + GLM 独立并行 | 三者均 APPROVE + hash 一致 |
| **仲裁复核** | `auto_trade_ai_arbitration.arbitrate_reviews()` | 同上 + 降级/重试 | 可选, unanimous_review 默认启用 |
| **理论复核** | `theoretical_review_all()` | 3AI 理论胜率≥50% + 止损簇≤0.30 | Codex 提交策略门禁 |
| **双引擎工厂** | `auto_trade_dual_engine_factory.py` | GLM 设计 + Codex 工程 + GLM 门控/模拟 | Step1–4 流水线 |
| **STEP A 工作流** | `dual_engine_workflow_v2/pipeline_step_a.py` | Gates 0–7 + 20 split tests | 机制指纹 + 失败 KB |
| **策略创造工厂** | `auto_trade_strategy_creation_factory.py` | 三 AI 协同创作 (非相互否决) | → 机器初筛 → 人工确认 |
| **系统预测** | `auto_trade_system_forecast.py` | DS 信号次数 + Qwen 活跃度 + GLM 综合 | 每周一定时 + Web 手动 |

### 2.4 双引擎工厂流水线 (Dual Engine)

文件: `auto_trade_dual_engine_factory.py`

```
Step1  Data → GLM insight → Codex hypothesis books → GLM audit
Step2  Codex DSL + anti-overfit / friction / logic destruction → GLM gate
Step3  GLM simulates DeepSeek/Qwen WR (≥55% each) → formal queue or repair≤3
Step4  Formal DeepSeek+Qwen theoretical WR (≥50% each) → pending human-confirm
```

常量:

```python
LEVERAGE = 20
STOP_LOSS_PCT = 0.009
SIM_WR_GATE = 55.0
FORMAL_WR_GATE = 50.0
```

产物路径:

- `/root/auto_trade/dual_engine/status.json`
- `/root/auto_trade/dual_engine/insight_latest.json`
- `/root/auto_trade/dual_engine/formal_submits.json`
- `/root/auto_trade/dual_engine/workflow_v2/artifacts/{task_id}_*.json`

### 2.5 STEP A 工作流 v2

文件: `dual_engine_workflow_v2/pipeline_step_a.py`

- Gates 0–7 (`gates.py`)
- 20-fold split tests (`split_tests_20.py`)
- 机制 spec + 指纹去重 (`mechanism_spec.py`, `step_a_fingerprint.py`)
- 失败知识库 (`failure_kb.py`)
- AI 攻击者: DeepSeek logic attack, production risk attack, GLM mechanism review, Codex fidelity review (`attackers.py`)

版本常量 (`config.py`):

```python
WORKFLOW_VERSION = "v2"
CODE_VERSION = "strategy_workflow_v2_step_a_20260726"
LEVERAGE = 20
STOP_LOSS_PCT = 0.009
INITIAL_POSITION_PCT = 0.30
TARGET_TRADES_PER_DAY = (0.5, 1.0)
```

### 2.6 系统预测 (System Forecast)

`auto_trade_system_forecast.py`:

- 采集所有 formal daemon 已挂载策略 (不限 B/A/S 名单)
- 三 AI 并行推演未来开仓频率
- 输出: `/root/auto_trade/system_forecast_latest.json`
- 历史: `/root/auto_trade/system_forecast_history.jsonl` (保留 52 周)
- `kind=system_forecast`, 不计策略复核配额

### 2.7 SAT_WITNESS 心跳

`auto_trade_strategy_ecosystem.py` 中 `_solvability_witness()` 返回确定性规则可满足性见证:

```python
return {"state": "SAT_WITNESS" if all(checks.values()) else "CONTRADICTION", ...}
```

检查项含: 机器初筛五折≥4/5、DD<40%、人工确认门禁、S/A/B/C 比例 (70/50/30/10%)、杠杆 20x、WxPusher 通道存在等。

进化循环 (`auto_trade_strategy_evolution.py`) 与生态工厂均依赖 SAT_WITNESS 心跳; 异常时禁止放松门禁。

---

## 3. 策略生命周期与引擎 (Strategy Lifecycle & Engine)

### 3.1 端到端生命周期

```
┌──────────────┐    ┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│ AI 创造/双引擎│ →  │ 机器初筛     │ →  │ WxPusher 人工│ →  │ B 级 30% 实盘│
│ STEP A /工厂 │    │ 5折/DD/死因  │    │ 确认 (S/A/B/C)│    │ 升降级 ladder│
└──────────────┘    └─────────────┘    └──────────────┘    └─────────────┘
                                              │
                                     ┌────────▼────────┐
                                     │ lifecycle 治理   │
                                     │ 评分/淘汰/轮换   │
                                     └─────────────────┘
```

### 3.2 人工确认流水线

文件: `auto_trade_human_confirm_pipeline.py`

```python
"""Create → machine screen → WxPusher push → human confirm → B(30%) live
Grades (equity ratio, leverage fixed 20x):
  S=70%  A=50%  B=30%  C=10%
Never auto-live without human confirm."""
```

关键常量:

| 常量 | 值 | 说明 |
|---|---|---|
| `LEVERAGE` | 20 | 固定杠杆 |
| `STOP_LOSS_PCT` | 0.009 | 0.9% 止损 |
| `MAX_DD` | 0.40 | 初筛最大回撤 |
| `MIN_TRADES_SCREEN` | 10 | 最少样本 |
| `MIN_WIN_RATE_SCREEN` | 50.0% | 真实成本下胜率 |
| `fee_rate_per_side` | 0.0005 (默认) | 单边费率 |

状态文件:

- `/root/auto_trade/strategy_pending_human_confirm.json`
- `/root/auto_trade/strategy_runtime_controls.json`
- `/root/auto_trade/human_confirm_pipeline_audit.jsonl`

### 3.3 策略 DSL 引擎

文件: `auto_trade_strategy_dsl.py`

- Schema: `qiyu_strategy_dsl_v1`
- 纯数据 DSL, 无 import/网络/文件访问
- 特征白名单: `close`, `ema6`–`ema200`, `k/d/j`, `cci`, `atr14`, `rsi14`, `z20`, `vol_z20` 等
- 标的白名单: 40+ OKX USDT-SWAP (含 BTC/ETH/SOL/ADA/XAU/NG/CL 等)
- 约束: `MAX_DEPTH=6`, `MAX_LEAVES=32`, `MAX_LOOKBACK=240`

身份哈希:

```python
def executable_hash(strategy):
    # schema + direction + timeframe + instruments + entry/exit/max_hold_bars
    return dsl_hash(value)
```

### 3.4 生命周期治理

文件: `auto_trade_strategy_lifecycle.py`

- 绑定 presence probe 与研究候选
- **不放宽**止损/退出管理/人工批准
- 允许 shadow→C 级条件频率探针自动晋级 (`conditional_frequency_probe`)
- 状态: `strategy_lifecycle_scores.json`, `strategy_lifecycle_status.json`

### 3.5 Formal Daemon 执行引擎

文件: `auto_trade_formal_daemon.py`

默认配置片段:

```python
DEFAULT_CONFIG = {
    "enabled": True,
    "allow_auto_open": False,      # 默认安全: 不自动开仓
    "allow_auto_close": False,
    "gate_authorized_auto_trading": True,
    "strategy_key": "ema6_center_down_then_fall",
    "symbol": TRADE_SYMBOL,        # env: VECTOR_TRADE_SYMBOL
    "timeframe": TRADE_TIMEFRAME,   # env: VECTOR_TRADE_TIMEFRAME
    "tick_interval_sec": 60,
    "cooldown_sec_after_open": 43200,
}
```

事件双写: `formal_daemon_events*.jsonl` + `auto_trade_strategy_events` 统一 taxonomy。

### 3.6 策略配置存储

| 路径 | 用途 |
|---|---|
| `/root/strategy_configs/experimental_strategies.json` | 实验/正式策略池 |
| `/root/strategy_configs/ai_dsl_strategies.json` | AI DSL 策略 |
| `/root/auto_trade/auto_trade_config.json` | 全局 auto_trade 配置 |
| `/root/auto_trade/formal_daemon_config*.json` | 各 daemon 实例 |

---

## 4. 数据管道 (Data Pipeline)

### 4.1 Parquet 市场数据

`backtest_engine_v2.py`:

```python
MARKET_DATA_ROOT = os.environ.get("VECTOR_MARKET_DATA_ROOT", "/root/market_data")

def _local_data_directories(inst_id, timeframe):
    directories = [os.path.join(MARKET_DATA_ROOT, inst_id, timeframe)]
    if inst_id == "BTC-USDT-SWAP" and timeframe == "1h":
        directories.append(os.path.join(MARKET_DATA_ROOT, "BTC-USDT", "1h"))
    return directories
```

目录约定:

```
/root/market_data/{INST_ID}/{TIMEFRAME}/*.parquet
# 例: /root/market_data/BTC-USDT-SWAP/5m/20260701.parquet
```

加载函数 `load_parquet_range(data_dir, start, end, timeframe)` — 目录不存在或为空时 **数据熔断** (FileNotFoundError)。

### 4.2 回测引擎

- 文件: `/root/backtest_engine_v2.py`
- 版本标识: `BACKTEST_VERSION = "backtest_engine_v2"` (workflow v2 config)
- Web 控制台通过 `/api/backtest` POST 触发, 进度 `/api/backtest_progress/<tid>`
- 数据覆盖查询: `/api/backtest/data_coverage`

### 4.3 摩擦/成本模型

生态模块 (`auto_trade_strategy_ecosystem.py`) 默认摩擦:

```python
"fee_rate_per_side": 0.0005
"slippage_rate_per_side": 0.0002
```

三重摩擦场景 (`human_confirm_pipeline`): `TRIPLE_FRICTION_SCENARIO = "severe"` — 仅作风险提示, **硬禁用** WxPusher 推送 (`TRIPLE_FRICTION_TIP_WX_ENABLED = False`)。

执行成本校准: `auto_trade_execution_cost_calibrator.py` (生产 flock 单实例, 只读遥测)。

### 4.4 微观结构 / 帧数据

- `auto_trade_microstructure_collector.py` — 原始 tick/深度采集
- `auto_trade_microstructure_primitives.py` — 基元特征 (systemd timer)
- Frost 系列脚本 (`frost3_*_run.py`) — 行为边缘发现, 输出 JSON 至 workspace

**UNVERIFIED**: 生产 `/root/market_data/` 各 symbol 最新 parquet 日期需现场 `ls` 确认。

---

## 5. 执行与遥测 (Execution & Telemetry)

### 5.1 实盘执行链路

```
formal_daemon tick
    → 信号评估 (strategy_key + DSL/legacy params)
    → execution precheck (web API 或内嵌)
    → OKX REST (下单/查仓/设止损)
    → event_ledger 追加
    → WxPusher 通知 (开仓/平仓/风控)
```

### 5.2 OKX 集成 (via web_server)

Web API 组 (均需 BasicAuth `quant`):

| 端点 | 方法 | 功能 |
|---|---|---|
| `/api/auto_trade/okx/status` | GET | 连接状态 |
| `/api/auto_trade/okx/balance` | GET | 余额 |
| `/api/auto_trade/okx/positions` | GET | 持仓 |
| `/api/auto_trade/okx/leverage` | GET | 杠杆 |
| `/api/auto_trade/okx/order_payload` | POST | 构造订单 |
| `/api/auto_trade/execution/live_one_order` | POST | 单笔实盘 |
| `/api/auto_trade/execution/live_dry_run` | POST | 干跑 |

凭证端点存在但**本报告不引用返回值** (含 API key)。

### 5.3 通知通道 (WxPusher)

文件: `auto_trade_formal_notify.py`

```python
REAL_NOTIFY_MODULE = "/root/common.py"
REAL_NOTIFY_FUNCTION = "send_wx"
WXPUSHER_URL = "https://wxpusher.zjiecode.com/api/send/message"
```

- 配置: `/root/auto_trade/formal_notify_config.json`
- 审计: `/root/auto_trade/formal_notification_audit.log`
- 硬拦截: `strategy_triple_friction_tip` 类通知永不允许推送
- 日终报告: `auto_trade_daily_report.py` — 23:59 幂等 WxPusher 汇总

### 5.4 事件账本与执行状态

| 模块 | 状态 API |
|---|---|
| `event_ledger` | `/api/auto_trade/event_ledger/status`, `/replay`, `/reconcile` |
| `execution_state` | `/api/auto_trade/execution_state/status`, `/self_test` |
| `formal/daemon` | `/api/auto_trade/formal/daemon/status`, `/tick`, `/start`, `/stop` |

Formal daemon 事件: `/root/auto_trade/formal_daemon_events*.jsonl`

### 5.5 遥测与指标

| 模块 | 输出 |
|---|---|
| `auto_trade_expectancy_metrics.py` | 期望收益指标 |
| `auto_trade_system_forecast.py` | 系统开仓频率预测 |
| `auto_trade_daily_report.py` | 日开平 ledger + Wx 报告 |
| `auto_trade_system_health_ai.py` | 异常 AI 诊断 → WxPusher |
| `auto_trade_execution_cost_calibrator.py` | 执行成本校准 JSON |

Web 指标 API:

- `/api/metrics/expectancy`
- `/api/metrics/ledgers`
- `/api/metrics/positive_expectancy_frequency`
- `/api/metrics/frequency_gap`
- `/api/forecast/*` (latest, history, calibration, run)

### 5.6 风控常量 (全系统一致)

| 参数 | 值 | 来源 |
|---|---|---|
| 杠杆 | **20x** (固定) | human_confirm, dual_engine, ecosystem |
| 主止损 | **0.9%** (`STOP_LOSS_PCT=0.009`) | 全局默认 |
| 辅止损 | **0.6%** | ecosystem 文档 (auxiliary) |
| 止盈 | **0.9%** | web_server auto_mode base_cfg |
| S/A/B/C 仓位比 | 70/50/30/10% | human_confirm + lifecycle |

---

## 6. Web API 与控制台 (Web APIs & Console)

### 6.1 服务入口

```python
# web_server.py
app.run(host="0.0.0.0", port=8080)
```

- 认证: HTTP Basic Auth (`flask_httpauth.HTTPBasicAuth`)
- 模板: `/root/template.html`, `/root/templates/forecast.html`
- 首页 `/` — 策略监控仪表盘
- `/forecast` — 系统预测页

### 6.2 API 路由分组 (共 ~126 个 `@app.route`)

#### A. 系统预测

| 路由 | 方法 |
|---|---|
| `/api/forecast/latest` | GET |
| `/api/forecast/history` | GET |
| `/api/forecast/calibration` | GET |
| `/api/forecast/run` | POST |
| `/api/forecast/run_status` | GET |
| `/api/forecast/refresh_statistical` | POST |

#### B. 双引擎工作流

| 路由 | 方法 |
|---|---|
| `/api/dual_engine/status` | GET |
| `/api/dual_engine/bootstrap` | POST |
| `/api/dual_engine/start_task` | POST |
| `/api/dual_engine/workflow` | GET |
| `/api/dual_engine/workflow/flags` | POST |
| `/api/dual_engine/evolve` | POST |
| `/api/dual_engine/refresh_insight` | POST |

#### C. 策略配置

| 路由 | 方法 |
|---|---|
| `/api/strategy_config` | GET |
| `/api/strategy_config/update` | POST |
| `/api/strategy_config/backup` | POST |
| `/api/strategy_config/export` | GET |
| `/api/strategy_desc` | GET |
| `/api/version_records` | GET |

#### D. 回测与修复

| 路由 | 方法 |
|---|---|
| `/api/backtest` | POST |
| `/api/backtest/data_coverage` | GET |
| `/api/backtest_progress/<tid>` | GET |
| `/api/repair` | POST |
| `/api/ai_fix_all` | POST |
| `/api/clear_signals` | POST |
| `/api/calibrate` | POST |

#### E. Auto Trade 核心

| 路由 | 方法 |
|---|---|
| `/api/auto_trade/config` | GET |
| `/api/auto_trade/start` / `pause` / `resume` | POST |
| `/api/auto_trade/health` | GET |
| `/api/auto_trade/health_summary` | GET |
| `/api/auto_trade/live_ready` | GET |
| `/api/auto_trade/reconcile` | POST |
| `/api/auto_trade/events` | GET |

#### F. Formal 正式实盘

| 路由 | 方法 |
|---|---|
| `/api/auto_trade/formal/status` | GET |
| `/api/auto_trade/formal/arm` | POST |
| `/api/auto_trade/formal/open` / `close` | POST |
| `/api/auto_trade/formal/daemon/*` | GET/POST |
| `/api/auto_trade/formal/gate/*` | GET/POST |
| `/api/auto_trade/formal/notify/*` | GET/POST |
| `/api/auto_trade/formal/auto_mode/*` | GET/POST (拦截器) |

Auto mode 三态 (`web_server.py`):

- `SAFE_INSTALLED` — 不开仓不平仓
- `AUTO_OPEN_ONLY` — 仅自动开仓
- `FULL_AUTO_LIVE_READY` — 全自动

#### G. 策略生态

| 路由 | 方法 |
|---|---|
| `/api/strategy-ecosystem/status` | GET |
| `/api/auto_trade/autonomous/*` | GET/POST |
| `/api/process_status` | GET |
| `/api/dialysis` | GET |

#### H. 指标

| 路由 | 方法 |
|---|---|
| `/api/metrics/expectancy` | GET |
| `/api/metrics/ledgers` | GET |
| `/api/metrics/positive_expectancy_frequency` | GET |
| `/api/metrics/frequency_gap` | GET |

### 6.3 控制台功能摘要

1. **策略监控** — 实时进程状态、信号、持仓
2. **回测实验室** — 提交回测、查看进度、数据覆盖
3. **系统预测** — 三 AI 开仓频率推演 + 手动触发
4. **双引擎控制台** — 启动 STEP A 任务、查看 workflow 状态
5. **Formal 实盘控制** — daemon 启停、gate 授权、auto mode 切换
6. **OKX 直连** — 余额/持仓/下单 (需 gate 授权)

---

## 7. Gemini 插件对接指南 (Gemini Plug-in Guide)

> 当前系统**未内置** Gemini provider。以下基于现有 DeepSeek/Qwen/GLM 插槽模式给出最小侵入对接路径。

### 7.1 架构定位建议

| 角色 | 建议 |
|---|---|
| 独立复核官 | 加入 `PROVIDERS` 四元组, 参与 `unanimous_review` |
| 机制审查 | 加入 `dual_engine_workflow_v2/attackers.py` 作为 `gemini_mechanism_review` |
| 研究/洞察 | 加入 `auto_trade_dual_engine_factory.glm_market_insight` 并列通道 |

**不推荐**替换 GLM 设计器角色 — GLM-5.2 已深度绑定 hypothesis/audit/gate 链路。

### 7.2 Step 1: 环境变量

在 `/root/auto_trade/ai_ecosystem.env` 追加 (**值由运维填入, 不入库**):

```bash
QIYU_GEMINI_API_KEY=<your-key>
QIYU_GEMINI_API_URL=https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
QIYU_GEMINI_MODEL=gemini-2.5-pro
```

Google 提供 OpenAI-compatible endpoint, 可复用现有 `_ai_json()` HTTP 逻辑。

### 7.3 Step 2: 扩展 `_provider_config()`

文件: `auto_trade_ai_consensus.py` (及 `auto_trade_dual_engine_factory._ai_json`)

```python
PROVIDERS = ("deepseek", "qwen", "glm", "gemini")  # 仅在明确需要四 AI 一致时

def _provider_config(name):
    defaults = {
        # ... existing ...
        "gemini": (
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "gemini-2.5-pro",
        ),
    }
```

`_normalize_provider_name()` 添加别名: `google`, `gemini-pro` → `gemini`。

### 7.4 Step 3: 同意书更新

编辑 `/root/auto_trade/ai_research_consent.json`:

```json
{
  "granted": true,
  "providers": ["deepseek", "qwen", "glm", "gemini"],
  "scopes": ["redacted_market_research_data", "redacted_trade_research_data"],
  "granted_at": "2026-07-29T..."
}
```

未更新 consent → `external_research_consent_status("gemini")` 返回 `allowed: false`。

### 7.5 Step 4: 仲裁策略选择

`unanimous_review()` 当前策略:

```python
"policy": "deepseek_and_qwen_and_glm_unanimous_exact_hash"
```

四 AI 方案二选一:

1. **严格四一致** — 修改 policy 字符串, 四方均 APPROVE
2. **3+1 顾问** — Gemini 仅写 audit, 不参与 unanimous (推荐初期)

若选方案 2, 在 `review_one()` 之外新增 `advisory_review_one("gemini", ...)` 写入 audit JSONL, 不改决策。

### 7.6 Step 5: 双引擎工厂接入

`auto_trade_dual_engine_factory._ai_json()` 已按 provider 分支。添加:

```python
if provider == "gemini":
    # same OpenAI-compatible payload as deepseek branch
    ...
```

可选新函数 `gemini_cross_review(book, packs)` 模拟第四视角, 输出写入 `dual_engine/audit.jsonl`。

### 7.7 Step 6: Web 暴露 (可选)

在 `web_server.py` 增加:

```python
@app.route("/api/ai/consensus/status", methods=["GET"])
def api_ai_consensus_status():
    import auto_trade_ai_consensus as c
    return jsonify({
        "providers": c.PROVIDERS,
        "credentials": c.credentials_status(),  # 无 secret
        "consent": c.external_research_consent_status(),
    })
```

### 7.8 Step 7: 验证清单

```bash
# 1. consent 检查
python3 -c "import auto_trade_ai_consensus as c; print(c.external_research_consent_status('gemini'))"

# 2. 凭证检查 (仅 ok/missing, 不打印 key)
python3 -c "import auto_trade_ai_consensus as c; print(c.credentials_status())"

# 3. 单条 review 冒烟 (使用 dummy candidate)
python3 -c "
import auto_trade_ai_consensus as c
dummy={'dsl':{'schema':'qiyu_strategy_dsl_v1'},'direction':'long'}
ev={'safety_metrics':{'empirical_win_rate':60}}
print(c.review_one('gemini', dummy, ev))
"

# 4. SAT_WITNESS 回归
python3 -c "import auto_trade_strategy_ecosystem as e; print(e._solvability_witness()['state'])"
```

### 7.9 预算与配额

策略创造工厂日预算: `BASE_DAILY_BUDGET_USD = 0.54`, 每 call 约 `$0.012`。

新增 Gemini 调用需更新 `auto_trade_strategy_creation_factory.budget_guard()` 计数, 避免超出日限额。

### 7.9 安全约束 (必须遵守)

1. **不得**将 Gemini 接入 formal_daemon 或 OKX 下单路径
2. **不得**绕过 `human_confirm` 直接 live
3. 所有 Gemini 请求走 `external_research_consent_status()` fail-closed
4. Prompt 仅发送 redacted 研究数据 (consent scope 限定)
5. 审计写入现有 JSONL (不新建无轮转日志)

---

## 附录 A: 关键 JSON 路径速查

| 路径 | 用途 |
|---|---|
| `/root/auto_trade/ai_research_consent.json` | AI 调用同意书 |
| `/root/auto_trade/ai_ecosystem.env` | API 密钥 (600) |
| `/root/auto_trade/auto_trade_config.json` | 全局配置 |
| `/root/auto_trade/strategy_runtime_controls.json` | 策略运行控制 |
| `/root/auto_trade/strategy_pending_human_confirm.json` | 待人工确认队列 |
| `/root/auto_trade/system_forecast_latest.json` | 最新系统预测 |
| `/root/auto_trade/dual_engine/status.json` | 双引擎状态 |
| `/root/strategy_configs/experimental_strategies.json` | 策略池 |
| `/root/docs/STEP_A_production_service_evidence.json` | STEP A 生产证据 |

## 附录 B: 本地 Workspace 模块索引

| 文件 | 职责 |
|---|---|
| `auto_trade_ai_consensus.py` | 三 AI 独立复核/研究/会审 |
| `auto_trade_ai_arbitration.py` | 复核仲裁/降级 |
| `auto_trade_dual_engine_factory.py` | GLM+Codex 双引擎工厂 |
| `dual_engine_workflow_v2/pipeline_step_a.py` | STEP A Gates 0–7 |
| `auto_trade_strategy_creation_factory.py` | 三 AI 协同创造 |
| `auto_trade_human_confirm_pipeline.py` | 人工确认 + B/C 监控 |
| `auto_trade_strategy_lifecycle.py` | 生命周期治理 |
| `auto_trade_strategy_ecosystem.py` | 生态工厂 + SAT_WITNESS |
| `auto_trade_system_forecast.py` | 系统预测 |
| `auto_trade_formal_daemon.py` | 正式实盘 daemon |
| `auto_trade_formal_notify.py` | WxPusher 通知 |
| `auto_trade_strategy_dsl.py` | 策略 DSL 引擎 |
| `backtest_engine_v2.py` | 回测 + Parquet |
| `web_server.py` | Flask 控制台 (~5269 行, 126 routes) |

---

*Report generated from live codebase audit + production SSH snapshot. No secrets included.*
