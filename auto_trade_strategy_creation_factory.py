# -*- coding: utf-8 -*-
"""三AI协同策略创造工厂（唯一核心创造模块）。

设计原则（2026-07-24）：
  · 「三个臭皮匠，顶个诸葛亮」——协同创作，不相互否决
  · 原独立能力全部内聚为子函数，不再作为独立模块调度
  · 产出 → 死因比对 → 机器初筛 → WxPusher 人工确认（不上自动实盘）

子能力映射：
  7  死亡热力图   → death_veto()
  8  微观基元     → micro_primitive_tags()（仅参考标签）
  9  三AI会审     → collaborative_generate() / peer_revise()
  11 成本监控     → budget_guard()
  15 生态位地图   → niche_guidance()（每周刷新，不强制）
  16 黑客松       → run_daily_collaborative_round()（协同非竞赛）
  17 工业化量产   → 移除随机组合；仅 AI 深度推理少量高质量候选
  18 冷冻库       → freezer_lessons()

已移除：环境准入门禁、策略呼吸控制器（由调用方停用）。
"""
from __future__ import print_function

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import tempfile
import time

ROOT = Path(os.environ.get("VECTOR_ROOT", "/root"))
AUTO_DIR = ROOT / "auto_trade"
STATE_PATH = AUTO_DIR / "strategy_creation_factory_state.json"
AUDIT_PATH = AUTO_DIR / "strategy_creation_factory_audit.jsonl"
BUDGET_PATH = AUTO_DIR / "strategy_creation_factory_budget.json"
PAUSE_AUTO_PATH = AUTO_DIR / "strategy_creation_factory_pause_auto.json"
MANUAL_JOB_PATH = AUTO_DIR / "strategy_creation_factory_manual_job.json"
MANUAL_COUNT_PATH = AUTO_DIR / "strategy_creation_factory_manual_count.json"
NICHE_CACHE = AUTO_DIR / "strategy_creation_factory_niche_weekly.json"

# Designer: daily API budget $0.54; Codex manual → temporary 2x
BASE_DAILY_BUDGET_USD = 0.54
MANUAL_BUDGET_MULTIPLIER = 2.0
# Rough per-call cost estimate for scheduling (not exact metering)
EST_COST_PER_CALL_USD = 0.012

PROVIDERS = ("deepseek", "qwen", "glm")
DRAFTS_PER_PROVIDER = 2  # auto: 2-3; use 2 for budget
DRAFTS_PER_PROVIDER_MANUAL = 3


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        delete=False, dir=str(path.parent), mode="w", encoding="utf-8")
    try:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        handle.close()
        Path(handle.name).replace(path)
    except Exception:
        try:
            Path(handle.name).unlink()
        except Exception:
            pass
        raise


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default


def _append_audit(row):
    path = Path(AUDIT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row, time=_now()), ensure_ascii=False) + "\n")


def _wx(text, kind="creation_factory", meta=None):
    try:
        import auto_trade_formal_notify as notify
        return notify.send_message(text, kind=kind, meta=meta or {})
    except Exception as exc:
        _append_audit({"event": "wx_fail", "error": str(exc),
                       "text_head": str(text)[:160]})
        return {"ok": False, "error": str(exc)}


# ─── 11 成本监控 / 算力调度 ───────────────────────────────────────────

def budget_state(manual=False):
    data = _read(BUDGET_PATH, {})
    day = _today()
    if data.get("day") != day:
        data = {
            "day": day,
            "spent_usd": 0.0,
            "calls": 0,
            "manual_boost_until": None,
            "base_cap_usd": BASE_DAILY_BUDGET_USD,
        }
    boost = False
    until = data.get("manual_boost_until")
    if manual or (until and str(until) >= _now()):
        boost = True
    cap = BASE_DAILY_BUDGET_USD * (MANUAL_BUDGET_MULTIPLIER if boost else 1.0)
    remaining = max(0.0, float(cap) - float(data.get("spent_usd") or 0))
    return {
        "day": day,
        "spent_usd": float(data.get("spent_usd") or 0),
        "calls": int(data.get("calls") or 0),
        "cap_usd": cap,
        "remaining_usd": remaining,
        "boost": boost,
        "manual_boost_until": until,
        "raw": data,
    }


def budget_allow(n_calls=1, manual=False):
    st = budget_state(manual=manual)
    need = float(n_calls) * EST_COST_PER_CALL_USD
    if need > st["remaining_usd"] + 1e-9:
        return False, st, "budget_exhausted"
    return True, st, "ok"


def budget_consume(n_calls=1, manual=False, note=""):
    ok, st, reason = budget_allow(n_calls=n_calls, manual=manual)
    if not ok:
        return {"ok": False, "reason": reason, "budget": st}
    raw = dict(st.get("raw") or {})
    raw["day"] = st["day"]
    raw["spent_usd"] = float(raw.get("spent_usd") or 0) + n_calls * EST_COST_PER_CALL_USD
    raw["calls"] = int(raw.get("calls") or 0) + int(n_calls)
    raw["base_cap_usd"] = BASE_DAILY_BUDGET_USD
    raw["last_note"] = note
    raw["updated_at"] = _now()
    if manual:
        raw["manual_boost_until"] = "%s 23:59:59" % _today()
    _atomic(BUDGET_PATH, raw)
    return {"ok": True, "budget": budget_state(manual=manual)}


def arm_manual_budget_boost():
    """Arm 2x daily budget for Codex manual accelerate (no call spend)."""
    st = budget_state(manual=False)
    raw = dict(st.get("raw") or {})
    raw["day"] = _today()
    raw["spent_usd"] = float(raw.get("spent_usd") or 0)
    raw["calls"] = int(raw.get("calls") or 0)
    raw["base_cap_usd"] = BASE_DAILY_BUDGET_USD
    raw["manual_boost_until"] = "%s 23:59:59" % _today()
    raw["updated_at"] = _now()
    raw["last_note"] = "manual_boost_arm"
    _atomic(BUDGET_PATH, raw)
    return budget_state(manual=True)


def pause_auto_mode(reason="codex_manual_accelerate", minutes=120):
    until = (datetime.now() + timedelta(minutes=int(minutes))).strftime(
        "%Y-%m-%d %H:%M:%S")
    flag = {
        "paused": True,
        "reason": reason,
        "paused_at": _now(),
        "resume_after": until,
    }
    _atomic(PAUSE_AUTO_PATH, flag)
    return flag


def auto_mode_paused():
    flag = _read(PAUSE_AUTO_PATH, {})
    if not flag.get("paused"):
        return False, flag
    resume = str(flag.get("resume_after") or "")
    if resume and resume < _now():
        flag["paused"] = False
        flag["auto_resumed_at"] = _now()
        _atomic(PAUSE_AUTO_PATH, flag)
        return False, flag
    return True, flag


# ─── 15 生态位地图（每周） ───────────────────────────────────────────

def niche_guidance(force=False):
    cache = _read(NICHE_CACHE, {})
    week = datetime.now().strftime("%Y-W%W")
    if (not force) and cache.get("week") == week and cache.get("report"):
        return cache.get("report") or {}
    try:
        import auto_trade_niche_map as niche
        report = niche.build_report(days=7)
    except Exception as exc:
        report = {"ok": False, "error": str(exc), "summary": {},
                  "mandatory_niches": []}
    cache = {"week": week, "updated_at": _now(), "report": report}
    _atomic(NICHE_CACHE, cache)
    return report


# ─── 7 死亡热力图 ───────────────────────────────────────────────────

def death_context():
    codes = []
    try:
        report = niche_guidance()
        for row in (report.get("death_heatmap_prior")
                    or report.get("top_death_codes") or []):
            if isinstance(row, dict):
                codes.append(row.get("code") or row.get("death_cause_code"))
            else:
                codes.append(str(row))
    except Exception:
        pass
    try:
        import auto_trade_mass_composer as composer
        death, none_of = composer._death_none_of()
        for item in (death or [])[:30]:
            if isinstance(item, dict):
                codes.append(item.get("code") or item.get("death_cause_code"))
        codes.extend(list(none_of or [])[:30])
    except Exception:
        pass
    # unique preserve order
    seen = set()
    out = []
    for c in codes:
        c = str(c or "").strip()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return {"death_codes": out[:40]}


def death_veto(dsl):
    """Return hit reason or None."""
    try:
        import auto_trade_human_confirm_pipeline as pipeline
        hit = pipeline._death_hard_fail(dsl)
        if hit:
            return hit
    except Exception:
        pass
    try:
        import auto_trade_mass_composer as composer
        death, none_of = composer._death_none_of()
        hit = composer._hits_death_isomorph(dsl, death)
        if hit:
            return hit
        text = json.dumps(dsl, ensure_ascii=False)
        for pid in none_of or []:
            if pid and pid in text:
                return "none_of:%s" % pid
    except Exception:
        pass
    return None


# ─── 18 冷冻库 / 失败案例 ───────────────────────────────────────────

def freezer_lessons(limit=20):
    lessons = []
    for path, key in (
        (AUTO_DIR / "strategy_failure_vault.json", "items"),
        (AUTO_DIR / "strategy_lifecycle_freeze_vault.json", "sealed"),
    ):
        data = _read(path, {})
        if key == "items":
            for item in list(data.get("items") or [])[-limit:]:
                row = item.get("row") or item
                lessons.append({
                    "source": "failure_vault",
                    "key": row.get("strategy_key") or item.get("assignment_id"),
                    "reason": item.get("reason") or row.get("delete_reason"),
                    "grade": row.get("lifecycle_grade"),
                })
        else:
            sealed = data.get("sealed") or {}
            if isinstance(sealed, dict):
                for aid, item in list(sealed.items())[-limit:]:
                    lessons.append({
                        "source": "lifecycle_freeze",
                        "key": aid,
                        "reason": (item or {}).get("reason") or (item or {}).get(
                            "seal_reason"),
                    })
    return lessons[-limit:]


# ─── 8 微观基元（参考标签，非硬条件） ───────────────────────────────

def micro_primitive_tags():
    try:
        import auto_trade_microstructure_primitives as micro
        if hasattr(micro, "list_registered_primitives"):
            rows = micro.list_registered_primitives() or []
        else:
            rows = []
        tags = []
        for row in rows[:40]:
            if isinstance(row, dict):
                tags.append(row.get("name") or row.get("primitive_id")
                            or row.get("id"))
            else:
                tags.append(str(row))
        return [t for t in tags if t][:30]
    except Exception:
        return []


# ─── 上下文打包 ─────────────────────────────────────────────────────

def build_factory_context(symbol=None, timeframe=None, days=7):
    """Assemble shared context for all three AIs."""
    dossier = {}
    try:
        import auto_trade_strategy_hackathon as hack
        dossier = hack.build_dossier(days=days) or {}
    except Exception as exc:
        dossier = {"ok": False, "error": str(exc)}
    niche = niche_guidance(force=False)
    death = death_context()
    freezer = freezer_lessons(limit=25)
    primitives = micro_primitive_tags()
    focus = {
        "symbol": symbol or (dossier.get("assignment") or {}).get("symbol"),
        "timeframe": timeframe or (dossier.get("assignment") or {}).get(
            "timeframe"),
    }
    # Prefer mandatory niches as soft guidance
    mandatory = []
    try:
        mandatory = list(niche.get("mandatory_niches")
                         or dossier.get("mandatory_niches") or [])[:5]
    except Exception:
        mandatory = []
    return {
        "schema": "qiyu_creation_factory_context_v1",
        "built_at": _now(),
        "focus": focus,
        "dossier": {
            "focus_clusters": dossier.get("focus_clusters"),
            "mandatory_niches": mandatory,
            "avoid_death_codes": dossier.get("avoid_death_codes")
            or death.get("death_codes"),
            "live_summary": (dossier.get("live_outcomes") or [])[:8]
            if isinstance(dossier.get("live_outcomes"), list) else dossier.get(
                "live_summary"),
        },
        "niche_summary": niche.get("summary") or {},
        "mandatory_niches": mandatory,
        "death_codes": death.get("death_codes") or [],
        "freezer_lessons": freezer,
        "micro_primitive_tags": primitives,
        "quality_priority": (
            "先保证逻辑稳健与胜率稳定，再谈频率；"
            "日均0.3次信号但胜率65% 远优于 日均3次但胜率45%；"
            "禁止追求高频信号；禁止为凑信号放宽入场。"
        ),
        "policy": (
            "winrate_stability_first;no_frequency_preference;"
            "deepseek_direction_only;qwen_draft;glm_attack_then_qwen_repair;"
            "death_hard_veto;empty_output_allowed;"
            "primitives_optional_tags;niche_soft_guidance;"
            "freezer_as_negative_examples;no_random_mass_compose"
        ),
    }


# ─── 9 三AI角色分工协同 ─────────────────────────────────────────────

QUALITY_RULE = (
    "生成策略时，优先保证逻辑的稳健性和胜率稳定性。"
    "信号频率低但质量高的策略，优于信号多但胜率不稳定的策略。"
    "一个日均只有0.3次信号但胜率65%的策略，远比日均3次信号但胜率45%的策略有价值。"
    "禁止任何「优先高频 / 提高开仓次数 / 凑交易频率」的偏好或要求。"
)

DEEPSEEK_DIRECTION_PROMPT = """你是栖语策略工厂的「深度推理官」DeepSeek。
职责：只提出方向，不写具体DSL，不凑数量。
输入：市场焦点、冷冻库失败案例、死因、生态位软指引、微观基元参考标签。
硬性规则：
1) """ + QUALITY_RULE + """
2) 分析当前环境下可能有效的策略逻辑方向（入场范式/过滤/失效条件），引用失败案例说明要规避什么。
3) 若一致认为当前环境无法提出任何逻辑自洽方向，输出 directions=[] 并说明 abstain_reason。
严格输出JSON：
{"directions":[{"title":"方向名","thesis":"为何可能有效","avoid":["规避点"],
"entry_idea":"入场思路","exit_idea":"出场思路","filters":["过滤思路"]}],
"abstain_reason":"仅当directions为空","notes":"..."}
"""

QWEN_DRAFT_PROMPT = """你是栖语策略工厂的「逻辑严谨官」Qwen。
职责：基于DeepSeek方向，生成2-3个逻辑自洽的完整策略草案（DSL）。
硬性规则：
1) """ + QUALITY_RULE + """
2) 主动规避冷冻库与死因；命中已知死因的方案不要提出。
3) 入场/出场/失效必须自洽；entry≥2个独立条件且含触发（cross_*或转折）；
   exit叶子带 role=take_profit|invalidation。
4) 输出完整 qiyu_strategy_dsl_v1；特征限 OHLC/EMA/KDJ/CCI/MACD/ATR/RSI/Z20/H1；
   运算符限 lt/lte/gt/gte/eq/between/cross_above/cross_below。
5) dsl顶层只允许：schema,key,name,direction,timeframe,supported_instruments,
   entry,exit,max_hold_bars,description；禁止 params/stop_loss/leverage。
6) 不得修改止损/杠杆/仓位；不得授予实盘资格。
7) 若方向无法落地为自洽DSL，drafts=[] 并说明原因（允许空输出）。
严格输出JSON：
{"drafts":[{"title":"...","thesis":"...","direction_ref":"方向名",
"env_tags":[],"death_lessons_avoided":[],"freezer_avoided":[],
"dsl":{完整DSL}}],"notes":"..."}
"""

OPENAI_ATTACK_PROMPT = """你是栖语策略工厂的「创意攻击官」OpenAI。
职责：对每个Qwen草案做反事实攻击——找出致命弱点，并为每个弱点提出一个改进变体。
硬性规则：
1) """ + QUALITY_RULE + """
2) 禁止只给通过/不通过；必须给出具体弱点与可执行改动。
3) 对每个草案至少指出1个致命弱点，并给出1个改进后的完整DSL变体。
4) DSL字段约束同Qwen；若草案不可救，variants=[]且discard=true并说明原因。
严格输出JSON：
{"reviews":[{"source_key":"原key","weaknesses":["..."],
"discard":false,"discard_reason":"",
"variants":[{"title":"...","thesis":"如何修补弱点","dsl":{完整DSL}}]}],
"notes":"..."}
"""

QWEN_REPAIR_PROMPT = """你是栖语策略工厂的「逻辑严谨官」Qwen（修正轮）。
职责：阅读OpenAI攻击意见，对草案做强制修正；输出最终候选。
硬性规则：
1) """ + QUALITY_RULE + """
2) 必须回应每个弱点；不能假装未见。
3) 输出修正后的完整DSL；若攻击成立且无法自洽修复，可丢弃该草案。
4) DSL字段约束不变。
严格输出JSON：
{"repaired":[{"action":"keep|revise|discard","source_key":"...",
"reason":"中文","title":"...","thesis":"...","dsl":{完整DSL或省略}}],
"notes":"..."}
"""

def _ai_json_call(provider, system_prompt, user_payload, max_tokens=2400):
    import auto_trade_ai_consensus as ai
    provider = ai._normalize_provider_name(provider)
    cfg = ai._provider_config(provider)
    consent = ai.external_research_consent_status(provider, "strategy_research")
    if not consent.get("allowed"):
        return {"ok": False, "error": "consent_missing", "provider": provider}
    if not cfg.get("api_key"):
        return {"ok": False, "error": "api_key_missing", "provider": provider}
    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": ai.canonical_json(user_payload)},
        ],
    }
    if provider == "deepseek":
        body["response_format"] = {"type": "json_object"}
    # glm/deepseek/qwen: OpenAI-compatible max_tokens (not OpenAI max_completion_tokens)
    body["temperature"] = 0.35
    body["max_tokens"] = int(max_tokens)
    started = time.time()
    try:
        import urllib.request as urllib_request
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            cfg["url"], data=encoded,
            headers={"Authorization": "Bearer " + cfg["api_key"],
                     "Content-Type": "application/json"}, method="POST")
        timeout = max(int(cfg.get("timeout") or 120), 180)
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        content = (((raw.get("choices") or [{}])[0].get("message") or {})
                   .get("content"))
        if not str(content or "").strip() and provider == "deepseek":
            content = (((raw.get("choices") or [{}])[0].get("message") or {})
                       .get("reasoning_content") or content)
        try:
            parsed = ai._parse_content_json(content)
        except Exception as parse_exc:
            # salvage fenced / trailing garbage once before failing the call
            text = str(content or "")
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(text[start:end + 1])
                except Exception:
                    return {"ok": False, "provider": provider,
                            "error": "json_parse:%s" % parse_exc,
                            "latency_sec": round(time.time() - started, 3),
                            "raw_preview": text[:400]}
            else:
                return {"ok": False, "provider": provider,
                        "error": "json_parse:%s" % parse_exc,
                        "latency_sec": round(time.time() - started, 3),
                        "raw_preview": text[:400]}
        return {
            "ok": True, "provider": provider, "parsed": parsed,
            "latency_sec": round(time.time() - started, 3),
        }
    except Exception as exc:
        return {"ok": False, "provider": provider, "error": str(exc),
                "latency_sec": round(time.time() - started, 3)}


def _sanitize_strategy_key(key, author="ai"):
    text = "".join(ch if (ch.isalnum() or ch == "_") else "_"
                   for ch in str(key or ""))
    text = text.strip("_")[:80]
    if not text:
        text = "factory_%s_%s" % (author, str(int(time.time()))[-6:])
    if text[0].isdigit():
        text = "s_" + text
    return text


def _map_feature_token(token):
    """Map AI-invented feature names onto the fixed DSL allow-list."""
    import auto_trade_strategy_dsl as dsl_mod
    import re
    text = str(token or "").strip()
    if text in dsl_mod.FEATURES:
        return text
    raw = text
    t = text.lower().replace(" ", "").replace("-", "_")
    t2 = t.replace(".", "_")
    aliases = {
        "close": "close", "open": "open", "high": "high", "low": "low",
        "z20": "z20", "rsi": "rsi14", "rsi14": "rsi14", "rsi(14)": "rsi14",
        "cci": "cci", "cci14": "cci", "cci_14": "cci", "cci(14)": "cci",
        "atr": "atr14", "atr14": "atr14", "atr(14)": "atr14",
        "macd": "macd_stick", "macd_hist": "macd_stick", "macd.hist": "macd_stick",
        "macdhist": "macd_stick", "macdstick": "macd_stick",
        "ema6": "ema6", "ema7": "ema7", "ema8": "ema8",
        "ema16": "ema16", "ema17": "ema17", "ema19": "ema19",
        "ema20": "ema21", "ema21": "ema21", "ema(20)": "ema21", "ema(21)": "ema21",
        "ema23": "ema23", "ema32": "ema32", "ema38": "ema38",
        "ema50": "ema53", "ema53": "ema53", "ema(50)": "ema53",
        "ema75": "ema75", "ema95": "ema95",
        "ema200": "ema200", "ema(200)": "ema200",
        "h1_close": "close", "h1_open": "open", "h1_high": "high", "h1_low": "low",
        "h1_ema19": "h1_ema19", "h1_ema50": "h1_ema53", "h1_ema53": "h1_ema53",
        "h1_ema(50)": "h1_ema53", "h1.ema(50)": "h1_ema53",
        "h1_ema200": "h1_ema53", "h1.ema(200)": "h1_ema53",
        "h1_ema_50": "h1_ema53", "h1_ema_200": "h1_ema53",
        "h1_rsi": "rsi14", "h1_rsi14": "rsi14", "h1.rsi(14)": "rsi14",
        "h1_atr14": "h1_atr14", "h1_slope4": "h1_slope4",
        "k": "k", "d": "d", "j": "j",
        "prev_high20": "prev_high20", "prev_low20": "prev_low20",
        "15m_close": "close", "15m_open": "open", "15m_cci_14": "cci",
        "15m_ema_50": "ema53", "15m_ema50": "ema53",
    }
    mapped = aliases.get(t) or aliases.get(t2) or aliases.get(raw.lower())
    if mapped and mapped in dsl_mod.FEATURES:
        return mapped
    # H1 EMA(N) before stripping prefix
    m = re.match(r"^h1[_.]?ema\(?([0-9]+)\)?$", t2)
    if m:
        n = int(m.group(1))
        feat = "h1_ema53" if n >= 36 else "h1_ema19"
        return feat
    # strip timeframe prefixes then retry
    t3 = t2
    for pref in ("15m_", "5m_", "1h_", "h1_", "tf_"):
        if t3.startswith(pref):
            t3 = t3[len(pref):]
            break
    mapped = aliases.get(t3)
    if mapped and mapped in dsl_mod.FEATURES:
        return mapped
    m = re.match(r"^ema\(?([0-9]+)\)?$", t3)
    if m:
        n = int(m.group(1))
        choices = [6, 7, 8, 16, 17, 19, 21, 23, 32, 38, 53, 75, 95, 200]
        nearest = min(choices, key=lambda x: abs(x - n))
        feat = "ema%d" % nearest
        if feat in dsl_mod.FEATURES:
            return feat
    return None


def _adapt_operand(value):
    """Convert AI operand (string/number/dict) to DSL operand."""
    if isinstance(value, dict):
        if "feature" in value:
            feat = _map_feature_token(value.get("feature"))
            if not feat:
                return None
            out = {"feature": feat}
            if value.get("offset") is not None:
                try:
                    out["offset"] = int(value.get("offset") or 0)
                except Exception:
                    out["offset"] = 0
            return out
        if "value" in value and len(value.keys()) == 1:
            try:
                return {"value": float(value.get("value"))}
            except Exception:
                return None
        # unknown dict shape
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"value": float(value)}
    text = str(value or "").strip()
    if not text:
        return None
    # composite expressions unsupported (EMA+ATR...)
    if any(ch in text for ch in ("+", "*", "/", "(", ")")) and not text.lower().startswith("ema"):
        # allow ema(20) style via mapper; reject arithmetic composites
        if any(op in text for op in ("+", "*", "/")):
            return None
    feat = _map_feature_token(text)
    if feat:
        return {"feature": feat}
    try:
        return {"value": float(text)}
    except Exception:
        return None


def _adapt_condition_node(node, prefix="c", counter=None, phase="entry"):
    """Recursively adapt AI DSL dialects into validate_strategy trees."""
    if counter is None:
        counter = [0]
    if not isinstance(node, dict):
        return None

    # Logical: {"op":"and"|"or", "args":[...]} / {"operator":"and","conditions":[...]}
    op_name = str(node.get("op") or node.get("operator") or "").lower()
    children = None
    if op_name in ("and", "all") and isinstance(node.get("args"), list):
        children = ("all", node.get("args"))
    elif op_name in ("or", "any") and isinstance(node.get("args"), list):
        children = ("any", node.get("args"))
    elif "all" in node and isinstance(node.get("all"), list):
        children = ("all", node.get("all"))
    elif "any" in node and isinstance(node.get("any"), list):
        children = ("any", node.get("any"))
    elif op_name in ("and", "all") and isinstance(node.get("conditions"), list):
        children = ("all", node.get("conditions"))
    elif op_name in ("or", "any") and isinstance(node.get("conditions"), list):
        children = ("any", node.get("conditions"))
    elif "conditions" in node and isinstance(node.get("conditions"), list):
        children = ("all", node.get("conditions"))
    if children:
        key, items = children
        adapted = []
        for child in items:
            leaf = _adapt_condition_node(child, prefix, counter, phase=phase)
            if leaf is not None:
                adapted.append(leaf)
        if not adapted:
            return None
        return {key: adapted}

    # Exit shorthand: {"take_profit": {...}, "invalidation": {...}}
    if "take_profit" in node or "invalidation" in node:
        parts = []
        for role_key in ("take_profit", "invalidation"):
            if role_key in node and isinstance(node.get(role_key), dict):
                leaf = _adapt_condition_node(node[role_key], prefix, counter,
                                            phase="exit")
                if isinstance(leaf, dict):
                    if "op" in leaf:
                        leaf["role"] = role_key
                    parts.append(leaf)
        if parts:
            return {"any": parts}

    # Compact {"gt": ["close", "ema21"]} already handled by hackathon; keep going
    # Leaf dialects:
    #  {"op":"gt","a":"...","b":...}
    #  {"feature":"...","operator":"lt","value":...}
    #  {"left":...,"op":...,"right":...}
    leaf_op = str(node.get("op") or node.get("operator") or "").lower()
    if leaf_op in ("lt", "lte", "gt", "gte", "eq", "between",
                   "cross_above", "cross_below"):
        left_raw = node.get("left", node.get("a", node.get("feature")))
        right_raw = node.get("right", node.get("b", node.get("value")))
        left = _adapt_operand(left_raw)
        if left is None:
            return None
        counter[0] += 1
        leaf = {
            "id": str(node.get("id") or ("%s%d" % (prefix, counter[0]))),
            "left": left,
            "op": leaf_op,
        }
        if leaf_op == "between":
            lower = node.get("lower")
            upper = node.get("upper")
            if lower is None and isinstance(right_raw, (list, tuple)) and len(right_raw) >= 2:
                lower, upper = right_raw[0], right_raw[1]
            try:
                leaf["lower"] = float(lower)
                leaf["upper"] = float(upper)
            except Exception:
                return None
        else:
            right = _adapt_operand(right_raw)
            if right is None:
                return None
            leaf["right"] = right
        role = node.get("role")
        if phase == "exit":
            if role in ("take_profit", "invalidation"):
                leaf["role"] = role
            else:
                leaf["role"] = "invalidation"
        return leaf

    # feature/operator/value without explicit op key already covered; try generic
    if "feature" in node and ("value" in node or "right" in node):
        fake = dict(node)
        fake["op"] = node.get("operator") or node.get("op") or "gt"
        return _adapt_condition_node(fake, prefix, counter, phase=phase)

    # Strip unknown keys and retry as logical if only all/any remain
    cleaned = {}
    for key, value in node.items():
        mapped = {"and": "all", "or": "any"}.get(str(key).lower(), key)
        if mapped in ("all", "any") and isinstance(value, list):
            adapted = [_adapt_condition_node(v, prefix, counter, phase=phase)
                       for v in value]
            adapted = [x for x in adapted if x is not None]
            if adapted:
                cleaned[mapped] = adapted
        elif mapped == "not":
            child = _adapt_condition_node(value, prefix, counter, phase=phase)
            if child is not None:
                cleaned["not"] = child
    if cleaned:
        return cleaned
    return None


def _coerce_dsl(dsl, author, context=None):
    """Strip unknown fields / force schema / adapt AI dialects."""
    import auto_trade_strategy_dsl as dsl_mod
    obj = dict(dsl or {})
    for drop in (
        "params", "stop_loss_pct", "leverage", "take_profit", "tp",
        "signal_score", "group", "enabled", "auto_trade_eligible",
        "recommended_leverage", "recommended_stop_loss_pct", "tags",
        "env_tags", "niche", "thesis", "title", "risk", "notes",
        "filters", "meta", "comment", "comments", "rationale",
    ):
        obj.pop(drop, None)
    obj["schema"] = dsl_mod.SCHEMA
    focus = (context or {}).get("focus") or {}
    if not obj.get("timeframe"):
        obj["timeframe"] = focus.get("timeframe") or "15m"
    if obj.get("timeframe") not in ("1h", "15m", "5m"):
        obj["timeframe"] = "15m"
    instruments = obj.get("supported_instruments")
    focus_sym = focus.get("symbol") or "BTC-USDT-SWAP"
    if not isinstance(instruments, list) or not instruments:
        obj["supported_instruments"] = [focus_sym]
    else:
        cleaned = [
            str(x) for x in instruments
            if str(x) in dsl_mod.INSTRUMENTS
        ]
        # Manual/focus runs: pin to requested symbol so screen matches accelerate target
        if focus_sym in dsl_mod.INSTRUMENTS:
            obj["supported_instruments"] = [focus_sym]
        else:
            obj["supported_instruments"] = cleaned or [focus_sym]
    if focus.get("timeframe") in ("1h", "15m", "5m"):
        obj["timeframe"] = focus.get("timeframe")
    if not obj.get("max_hold_bars"):
        obj["max_hold_bars"] = 48 if obj.get("timeframe") == "5m" else 32
    try:
        hold = int(obj.get("max_hold_bars") or 0)
    except Exception:
        hold = 32
    obj["max_hold_bars"] = max(1, min(240, hold))
    obj["key"] = _sanitize_strategy_key(obj.get("key") or obj.get("name"),
                                        author=author)
    if not obj.get("name"):
        obj["name"] = obj["key"]
    if not obj.get("direction") and isinstance(obj.get("side"), str):
        side = str(obj.pop("side")).lower()
        obj["direction"] = "short" if "short" in side else "long"
    obj.pop("side", None)

    # Adapt entry/exit dialects before leaf normalize
    if "entry" in obj:
        adapted = _adapt_condition_node(obj.get("entry") or {}, "e", [0],
                                        phase="entry")
        if adapted is not None:
            obj["entry"] = adapted
    if "exit" in obj:
        adapted = _adapt_condition_node(obj.get("exit") or {}, "x", [0],
                                        phase="exit")
        if adapted is not None:
            obj["exit"] = adapted

    allowed = {"schema", "key", "name", "direction", "timeframe",
               "supported_instruments", "entry", "exit", "max_hold_bars",
               "description", "origin", "version", "live_enabled",
               "approved_version_hash", "auto_trade_eligible"}
    obj = {k: v for k, v in obj.items() if k in allowed}
    try:
        import auto_trade_strategy_hackathon as hack
        if hasattr(hack, "_normalize_dsl"):
            obj = hack._normalize_dsl(obj)
    except Exception:
        pass
    # Final leaf scrub: keep only allowed condition keys
    def _scrub(node, phase):
        if not isinstance(node, dict):
            return node
        if any(k in node for k in ("all", "any", "not")):
            out = {}
            if "all" in node:
                out["all"] = [_scrub(x, phase) for x in (node.get("all") or [])]
            if "any" in node:
                out["any"] = [_scrub(x, phase) for x in (node.get("any") or [])]
            if "not" in node:
                out["not"] = _scrub(node.get("not"), phase)
            return out
        keep = {"id", "left", "op", "right", "lower", "upper"}
        if phase == "exit":
            keep.add("role")
        out = {k: v for k, v in node.items() if k in keep}
        # scrub operands
        for side in ("left", "right"):
            opnd = out.get(side)
            if isinstance(opnd, dict):
                if "feature" in opnd:
                    feat = _map_feature_token(opnd.get("feature"))
                    if feat:
                        cleaned = {"feature": feat}
                        if opnd.get("offset"):
                            cleaned["offset"] = int(opnd.get("offset") or 0)
                        out[side] = cleaned
                    else:
                        out[side] = {"feature": "close"}
                elif "value" in opnd:
                    try:
                        out[side] = {"value": float(opnd.get("value"))}
                    except Exception:
                        out[side] = {"value": 0.0}
        return out
    if "entry" in obj:
        obj["entry"] = _scrub(obj["entry"], "entry")
    if "exit" in obj:
        obj["exit"] = _scrub(obj["exit"], "exit")
    return obj


def _normalize_draft(draft, author, context=None):
    if not isinstance(draft, dict):
        return None
    dsl = draft.get("dsl")
    if not isinstance(dsl, dict):
        return None
    try:
        import auto_trade_strategy_dsl as dsl_mod
        dsl = _coerce_dsl(dsl, author, context=context)
        dsl = dsl_mod.validate_strategy(dsl)
    except Exception as exc:
        return {"ok": False, "error": "dsl_invalid:%s" % exc, "raw": draft,
                "author": author}
    return {
        "ok": True,
        "author": author,
        "title": draft.get("title") or dsl.get("name") or dsl.get("key"),
        "thesis": draft.get("thesis"),
        "env_tags": draft.get("env_tags") or [],
        "niche": draft.get("niche"),
        "death_lessons_avoided": draft.get("death_lessons_avoided") or [],
        "freezer_avoided": draft.get("freezer_avoided") or [],
        "dsl": dsl,
    }


def deepseek_propose_directions(context, manual=False):
    ok, st, reason = budget_allow(n_calls=1, manual=manual)
    if not ok:
        return {"ok": False, "error": reason, "directions": [], "budget": st}
    payload = {
        "role": "deepseek_direction_only",
        "context": context,
        "instruction": "只给方向，不写DSL；质量优先，禁止频率偏好。",
    }
    res = _ai_json_call("deepseek", DEEPSEEK_DIRECTION_PROMPT, payload,
                        max_tokens=1800)
    if not res.get("ok") and res.get("error") not in (
            "api_key_missing", "consent_missing"):
        res = _ai_json_call("deepseek", DEEPSEEK_DIRECTION_PROMPT, payload,
                            max_tokens=2200)
    if res.get("ok"):
        budget_consume(n_calls=1, manual=manual, note="deepseek_direction")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "directions": [],
                "latency_sec": res.get("latency_sec")}
    parsed = res.get("parsed") or {}
    directions = parsed.get("directions") or []
    if not isinstance(directions, list):
        directions = []
    return {
        "ok": True,
        "directions": directions[:5],
        "abstain_reason": parsed.get("abstain_reason"),
        "notes": parsed.get("notes"),
        "latency_sec": res.get("latency_sec"),
    }


def qwen_generate_drafts(context, directions, n_drafts=2, manual=False):
    ok, st, reason = budget_allow(n_calls=1, manual=manual)
    if not ok:
        return {"ok": False, "error": reason, "drafts": [], "budget": st}
    payload = {
        "role": "qwen_draft",
        "n_drafts": int(n_drafts),
        "directions": directions,
        "context": context,
        "focus": context.get("focus"),
        "instruction": (
            "基于方向生成 %d 个严密草案；质量优先；允许 drafts=[]。"
            % int(n_drafts)
        ),
    }
    res = _ai_json_call("qwen", QWEN_DRAFT_PROMPT, payload, max_tokens=3200)
    if not res.get("ok") and res.get("error") not in (
            "api_key_missing", "consent_missing"):
        res = _ai_json_call("qwen", QWEN_DRAFT_PROMPT, payload, max_tokens=3600)
    if res.get("ok"):
        budget_consume(n_calls=1, manual=manual, note="qwen_draft")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "drafts": []}
    parsed = res.get("parsed") or {}
    drafts = parsed.get("drafts") or []
    normalized = []
    for d in drafts[: max(1, int(n_drafts)) + 1]:
        item = _normalize_draft(d, "qwen", context=context)
        if item:
            normalized.append(item)
    return {
        "ok": True,
        "drafts": normalized,
        "notes": parsed.get("notes"),
        "latency_sec": res.get("latency_sec"),
    }


def glm_attack_drafts(context, drafts, manual=False):
    ok, st, reason = budget_allow(n_calls=1, manual=manual)
    if not ok:
        return {"ok": False, "error": reason, "reviews": [], "budget": st}
    slim = []
    for d in drafts:
        slim.append({
            "key": (d.get("dsl") or {}).get("key"),
            "title": d.get("title"),
            "thesis": d.get("thesis"),
            "dsl": d.get("dsl"),
        })
    payload = {
        "role": "glm_attack",
        "drafts": slim,
        "context_slice": {
            "death_codes": context.get("death_codes"),
            "freezer_lessons": context.get("freezer_lessons"),
            "quality_priority": context.get("quality_priority"),
        },
        "instruction": "反事实攻击每个草案，并给出改进变体；禁止简单投票。",
    }
    res = _ai_json_call("glm", OPENAI_ATTACK_PROMPT, payload, max_tokens=3600)
    if not res.get("ok") and res.get("error") not in (
            "api_key_missing", "consent_missing"):
        res = _ai_json_call("glm", OPENAI_ATTACK_PROMPT, payload,
                            max_tokens=4000)
    if res.get("ok"):
        budget_consume(n_calls=1, manual=manual, note="glm_attack")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "reviews": []}
    parsed = res.get("parsed") or {}
    reviews = parsed.get("reviews") or []
    out_reviews = []
    for rev in reviews:
        if not isinstance(rev, dict):
            continue
        variants = []
        for v in (rev.get("variants") or []):
            item = _normalize_draft(v, "glm", context=context)
            if item:
                variants.append(item)
        out_reviews.append({
            "source_key": rev.get("source_key"),
            "weaknesses": rev.get("weaknesses") or [],
            "discard": bool(rev.get("discard")),
            "discard_reason": rev.get("discard_reason"),
            "variants": variants,
        })
    return {
        "ok": True,
        "reviews": out_reviews,
        "notes": parsed.get("notes"),
        "latency_sec": res.get("latency_sec"),
    }


def qwen_repair_after_attack(context, drafts, reviews, manual=False):
    ok, st, reason = budget_allow(n_calls=1, manual=manual)
    if not ok:
        return {"ok": False, "error": reason, "repaired": [], "budget": st}
    payload = {
        "role": "qwen_repair",
        "drafts": [
            {"key": (d.get("dsl") or {}).get("key"), "title": d.get("title"),
             "thesis": d.get("thesis"), "dsl": d.get("dsl")}
            for d in drafts
        ],
        "reviews": [
            {"source_key": r.get("source_key"),
             "weaknesses": r.get("weaknesses"),
             "discard": r.get("discard"),
             "discard_reason": r.get("discard_reason"),
             "variant_dsls": [
                 v.get("dsl") for v in (r.get("variants") or []) if v.get("ok")
             ][:2]}
            for r in reviews
        ],
        "instruction": "必须经历攻击后的修正；回应弱点；允许丢弃。",
    }
    res = _ai_json_call("qwen", QWEN_REPAIR_PROMPT, payload, max_tokens=3200)
    if not res.get("ok") and res.get("error") not in (
            "api_key_missing", "consent_missing"):
        res = _ai_json_call("qwen", QWEN_REPAIR_PROMPT, payload, max_tokens=3600)
    if res.get("ok"):
        budget_consume(n_calls=1, manual=manual, note="qwen_repair")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error"), "repaired": []}
    parsed = res.get("parsed") or {}
    repaired = []
    for row in (parsed.get("repaired") or []):
        action = str(row.get("action") or "revise").lower()
        if action == "discard":
            _append_audit({"event": "qwen_repair_discard",
                           "source_key": row.get("source_key"),
                           "reason": row.get("reason")})
            continue
        item = _normalize_draft(row, "qwen", context=context)
        if item and item.get("ok"):
            item["repair_action"] = action
            item["repair_reason"] = row.get("reason")
            item["attack_cycle"] = "qwen→glm→qwen"
            repaired.append(item)
        elif item:
            repaired.append(item)
    return {
        "ok": True,
        "repaired": repaired,
        "notes": parsed.get("notes"),
        "latency_sec": res.get("latency_sec"),
    }


def collaborative_round(context, n_drafts=2, manual=False, quality_boost=False):
    """Role pipeline: DeepSeek方向 → Qwen草案 → OpenAI攻击 → Qwen修正."""
    n_drafts = int(n_drafts or 2)
    if quality_boost:
        context = dict(context or {})
        context["quality_boost"] = True
        context["quality_priority"] = (
            (context.get("quality_priority") or "")
            + " 本轮强化：入场条件必须更严苛；宁可少信号；目标回测胜率倾向≥55%。"
        )

    ok, st, reason = budget_allow(n_calls=4, manual=manual)
    if not ok:
        return {"ok": False, "error": reason, "budget": st, "candidates": []}

    direction = deepseek_propose_directions(context, manual=manual)
    directions = direction.get("directions") or []
    if not directions:
        hard_err = direction.get("error")
        # True abstain (AI says no direction) is empty-ok; infra failures are not.
        infra_fail = bool(hard_err) and hard_err not in (
            "deepseek_no_direction",)
        out = {
            "ok": not infra_fail,
            "empty_allowed": not infra_fail,
            "error": hard_err if infra_fail else None,
            "reason": direction.get("abstain_reason")
            or hard_err
            or "deepseek_no_direction",
            "direction": direction,
            "n_drafts_in": 0,
            "n_after_revise": 0,
            "candidates": [],
            "budget": budget_state(manual=manual),
            "generate_summary": [
                {"provider": "deepseek", "role": "direction",
                 "ok": direction.get("ok"), "n_directions": 0,
                 "error": hard_err or direction.get("abstain_reason")},
            ],
        }
        _append_audit({"event": "collaborative_empty", "reason": out["reason"],
                       "infra_fail": infra_fail})
        return out

    qwen = qwen_generate_drafts(context, directions, n_drafts=n_drafts,
                                manual=manual)
    drafts = [d for d in (qwen.get("drafts") or []) if d.get("ok")]
    if not drafts:
        return {
            "ok": True,
            "empty_allowed": True,
            "reason": qwen.get("notes") or qwen.get("error") or "qwen_no_valid_dsl",
            "direction": direction,
            "n_drafts_in": 0,
            "n_after_revise": 0,
            "candidates": [],
            "budget": budget_state(manual=manual),
            "generate_summary": [
                {"provider": "deepseek", "role": "direction",
                 "n_directions": len(directions)},
                {"provider": "qwen", "role": "draft",
                 "n_raw": len(qwen.get("drafts") or []), "n_valid": 0},
            ],
        }

    attack = glm_attack_drafts(context, drafts, manual=manual)
    reviews = attack.get("reviews") or []
    if not attack.get("ok"):
        reviews = [{"source_key": (d.get("dsl") or {}).get("key"),
                    "weaknesses": ["attack_call_failed_please_self_critique"],
                    "variants": []} for d in drafts]

    repair = qwen_repair_after_attack(context, drafts, reviews, manual=manual)
    repaired = [d for d in (repair.get("repaired") or []) if d.get("ok")]
    if not repaired:
        for rev in reviews:
            for v in (rev.get("variants") or []):
                if v.get("ok"):
                    v = dict(v)
                    v["attack_cycle"] = "glm_variant_fallback"
                    repaired.append(v)

    survivors = []
    for draft in repaired:
        dsl = draft.get("dsl") or {}
        hit = death_veto(dsl)
        if hit:
            _append_audit({"event": "death_veto", "key": dsl.get("key"),
                           "hit": hit, "author": draft.get("author")})
            continue
        survivors.append(draft)

    return {
        "ok": True,
        "pipeline": "deepseek_direction→qwen_draft→glm_attack→qwen_repair",
        "direction": {
            "n": len(directions),
            "titles": [d.get("title") for d in directions if isinstance(d, dict)],
            "abstain_reason": direction.get("abstain_reason"),
        },
        "n_drafts_in": len(drafts),
        "n_after_revise": len(repaired),
        "n_attack_reviews": len(reviews),
        "candidates": survivors,
        "budget": budget_state(manual=manual),
        "empty_allowed": len(survivors) == 0,
        "generate_summary": [
            {"provider": "deepseek", "role": "direction",
             "ok": direction.get("ok"), "n_directions": len(directions)},
            {"provider": "qwen", "role": "draft",
             "ok": qwen.get("ok"), "n_raw": len(qwen.get("drafts") or []),
             "n_valid": len(drafts)},
            {"provider": "glm", "role": "attack",
             "ok": attack.get("ok"), "n_reviews": len(reviews),
             "error": attack.get("error")},
            {"provider": "qwen", "role": "repair",
             "ok": repair.get("ok"), "n_repaired": len(repaired),
             "error": repair.get("error")},
        ],
    }


def screen_and_push(candidates, source="creation_factory"):
    """Send survivors into human-confirm machine screen → Wx push.

    Phase-3 additive: apply L2 Pareto front filter across fitness-passing
    candidates before ingest (non-dominated only). Does not auto-mount.
    Phase-4: attach incubator card fields when present; production_mounted=False.
    """
    import auto_trade_human_confirm_pipeline as pipeline
    rows = list(candidates or [])
    # Optional Phase-3 Pareto cull when candidates carry fitness/trades metrics
    try:
        from dual_engine_workflow_v2.phase3_funnel import apply_pareto_to_batch
        scored = []
        for cand in rows:
            trades = cand.get("trades") or []
            base_m = cand.get("base_metrics") or cand.get("metrics") or {}
            if trades or base_m.get("calmar") is not None or base_m.get("payoff_ratio") is not None:
                scored.append({
                    "id": (cand.get("dsl") or {}).get("key") or cand.get("key") or id(cand),
                    "trades": trades,
                    "base_metrics": base_m,
                    "calmar": base_m.get("calmar"),
                    "payoff_ratio": base_m.get("payoff_ratio") or base_m.get("payoff"),
                    "trade_frequency": base_m.get("trade_frequency") or base_m.get("trades") or len(trades),
                    "fitness_pass": cand.get("fitness_pass", True),
                    "_cand": cand,
                })
        if scored and all(s.get("calmar") is not None for s in scored):
            front = apply_pareto_to_batch(scored)
            keep = set(front.get("front_ids") or [])
            rows = [s["_cand"] for s in (front.get("candidates") or []) if s.get("id") in keep]
    except Exception:
        rows = list(candidates or [])

    results = []
    for cand in rows:
        dsl = cand.get("dsl") or {}
        symbol = (dsl.get("supported_instruments") or [None])[0]
        timeframe = dsl.get("timeframe")
        incub = cand.get("phase4_incubator") or {}
        base_m = cand.get("base_metrics") or cand.get("metrics") or {}
        ai_review = cand.get("ai_review")
        if ai_review and isinstance(ai_review, dict):
            # Enrich existing review with Phase-4 incubator card fields
            ai_review = dict(ai_review)
            if ai_review.get("calmar") is None:
                ai_review["calmar"] = incub.get("calmar") or base_m.get("calmar")
            if ai_review.get("payoff") is None:
                ai_review["payoff"] = (
                    incub.get("payoff_ratio") or base_m.get("payoff_ratio")
                )
            if ai_review.get("cross_asset_score") is None:
                ai_review["cross_asset_score"] = incub.get("cross_asset_score")
            if ai_review.get("mean_mae") is None:
                ai_review["mean_mae"] = incub.get("mean_mae")
            ai_review["phase4_incubator"] = bool(incub) or ai_review.get("phase4_incubator")
        out = pipeline.ingest_and_screen(
            {"dsl": dsl, "symbol": symbol, "timeframe": timeframe,
             "phase4_incubator": incub, "production_mounted": False},
            source="%s:%s" % (source, cand.get("author") or "ai"),
            ai_review=ai_review,
        )
        results.append({
            "key": dsl.get("key"),
            "author": cand.get("author"),
            "title": cand.get("title"),
            "screen": out,
            "production_mounted": False,
        })
    return results


def _screened_all_winrate_below_50(screened):
    """True when screened candidates exist and none reach WR≥50% with trades>0.

    Zero-trade / unmeasurable candidates count as below the bar so the round
    can be voided and quality-boosted once.
    """
    rows = list(screened or [])
    if not rows:
        return False
    any_ok = False
    for row in rows:
        scr = row.get("screen") or {}
        metrics = scr.get("metrics") or {}
        trades = int(metrics.get("trades") or 0)
        try:
            wr = float(metrics.get("win_rate") or 0.0)
        except Exception:
            wr = 0.0
        if trades > 0 and wr >= 50.0:
            any_ok = True
            break
    return not any_ok


def run_quality_guarded_round(context, n_drafts=2, manual=False, source="factory"):
    """Run collaborative round + screen; void&retry once if all WR<50%."""
    attempts = []
    screened = []
    collab = {}
    for attempt in range(2):
        quality_boost = attempt > 0
        collab = collaborative_round(
            context, n_drafts=n_drafts, manual=manual,
            quality_boost=quality_boost)
        screened = []
        infra_fail = (not collab.get("ok")) and bool(collab.get("error"))
        if collab.get("ok"):
            screened = screen_and_push(
                collab.get("candidates") or [],
                source="%s_a%d" % (source, attempt + 1))
        attempts.append({
            "attempt": attempt + 1,
            "quality_boost": quality_boost,
            "n_candidates": len(collab.get("candidates") or []),
            "generate_summary": collab.get("generate_summary"),
            "voided": False,
            "infra_fail": infra_fail,
            "error": collab.get("error"),
            "screened": [
                {"key": s.get("key"),
                 "reason": (s.get("screen") or {}).get("reason"),
                 "win_rate": ((s.get("screen") or {}).get("metrics") or {}).get(
                     "win_rate"),
                 "trades": ((s.get("screen") or {}).get("metrics") or {}).get(
                     "trades"),
                 "pushed": (s.get("screen") or {}).get("pushed")}
                for s in screened
            ],
        })
        should_retry = False
        if infra_fail and attempt == 0:
            should_retry = True
            attempts[-1]["voided"] = True
            attempts[-1]["void_reason"] = "infra_fail_retry"
        elif _screened_all_winrate_below_50(screened):
            should_retry = attempt == 0
            attempts[-1]["voided"] = True
            attempts[-1]["void_reason"] = "all_winrate_below_50"
            _append_audit({
                "event": "round_void_low_winrate",
                "attempt": attempt + 1,
                "screened": attempts[-1]["screened"],
            })
            try:
                import auto_trade_human_confirm_pipeline as pipeline
                pending = pipeline.load_pending()
                keys = {s.get("key") for s in screened}
                kept = []
                for item in pending.get("items") or []:
                    if (item.get("key") in keys
                            and item.get("status") == "awaiting_confirm"
                            and str(item.get("source") or "").startswith(source)):
                        item = dict(item)
                        item["status"] = "voided_low_winrate"
                        item["voided_at"] = _now()
                    kept.append(item)
                pending["items"] = kept
                pipeline.save_pending(pending)
            except Exception:
                pass
            screened = []
        if should_retry:
            continue
        break
    return {
        "ok": collab.get("ok", True),
        "collab": collab,
        "screened": screened,
        "attempts": attempts,
        "error": collab.get("error"),
    }


# ─── 主流程：自动每日协同 / Codex手动加速 ───────────────────────────

def run_daily_collaborative_round(force=False):
    """DISABLED 2026-07-24: 三AI不再造策略，仅复核/运维。"""
    out = {
        "ok": True,
        "disabled": True,
        "reason": "disabled_ai_creation",
        "mode": "ai_review_ops_only",
        "time": _now(),
        "natural_language": (
            "策略创造工厂已停用：三AI改为复核与运维。"
            "新策略请由 Codex 开发后经 auto_trade_codex_strategy_review.py --submit 提交。"
        ),
        "force_ignored": bool(force),
    }
    try:
        _atomic(STATE_PATH, out)
    except Exception as exc:
        out["state_write_error"] = str(exc)
    return out
    # --- legacy path retained below but unreachable ---
    paused, flag = auto_mode_paused()
    if paused and not force:
        out = {"ok": True, "skipped": "auto_paused", "pause": flag, "time": _now()}
        _atomic(STATE_PATH, out)
        return out
    context = build_factory_context(days=7)
    guarded = run_quality_guarded_round(
        context, n_drafts=DRAFTS_PER_PROVIDER, manual=False,
        source="factory_daily")
    collab = guarded.get("collab") or {}
    if not guarded.get("ok") and collab.get("error"):
        _atomic(STATE_PATH, dict(collab, time=_now(), mode="auto"))
        return collab
    screened = guarded.get("screened") or []
    pushed = sum(1 for r in screened if (r.get("screen") or {}).get("pushed"))
    out = {
        "ok": True,
        "mode": "auto_daily_collaborative",
        "time": _now(),
        "n_candidates": len(collab.get("candidates") or []),
        "n_pushed": pushed,
        "screened": screened,
        "attempts": guarded.get("attempts"),
        "pipeline": collab.get("pipeline"),
        "budget": collab.get("budget"),
        "policy": context.get("policy"),
        "natural_language": (
            "三AI角色日轮完成（DeepSeek方向→Qwen草案→OpenAI攻击→Qwen修正）："
            "候选 %d，初筛推送 %d。质量优先，允许空输出。"
            % (len(collab.get("candidates") or []), pushed)
        ),
    }
    _atomic(STATE_PATH, out)
    _append_audit({"event": "daily_round", "out": {
        "n_candidates": out["n_candidates"], "n_pushed": pushed}})
    if pushed:
        _wx(
            "【三AI协同日轮】\n候选 %d · 已推送待确认 %d\n时间: %s"
            % (out["n_candidates"], pushed, _now()),
            kind="factory_daily",
            meta={"pushed": pushed},
        )
    # Dynamic optimizer is scheduled on its own UTC-00:00 timer; status only.
    try:
        import auto_trade_strategy_dynamic_optimizer as optimizer
        out["optimizer_status"] = {
            "pending_n": optimizer.status().get("pending_n"),
            "note": "daily_explore_via_qiyu-strategy-dynamic-optimizer.timer",
        }
    except Exception as exc:
        out["optimizer_status_error"] = str(exc)
    return out


def _manual_accelerate_seq(bump=True):
    """Return today's manual-accelerate ordinal (1-based). Persist across runs."""
    day = _today()
    data = _read(MANUAL_COUNT_PATH, {})
    if data.get("day") != day:
        data = {"day": day, "count": 0, "runs": []}
    if bump:
        data["count"] = int(data.get("count") or 0) + 1
        data["updated_at"] = _now()
        _atomic(MANUAL_COUNT_PATH, data)
    return int(data.get("count") or 0)


def _format_manual_done_wx(seq, symbol, timeframe, rounds, pushed, finished_at=None):
    """Human-readable completion notice."""
    day = _today()
    finished_at = finished_at or _now()
    head = "%s 第%d次加速已完成。" % (day, int(seq))
    if int(pushed or 0) <= 0:
        body = "加速流程已完成，但暂无可确认上线的策略。"
    else:
        body = (
            "加速流程已完成，已推送 %d 条策略待你确认上线。"
            "确认命令: python3 auto_trade_human_confirm_pipeline.py --confirm KEY"
            % int(pushed)
        )
    return (
        "%s\n"
        "%s\n"
        "标的/周期: %s / %s · 轮数: %d\n"
        "时间: %s"
        % (head, body, symbol, timeframe, int(rounds), finished_at)
    )


def run_manual_accelerate(symbol, timeframe, rounds=1, n_drafts=None):
    """DISABLED 2026-07-24: 不再用三AI手动画策略。"""
    return {
        "ok": True,
        "disabled": True,
        "reason": "disabled_ai_creation",
        "symbol": symbol,
        "timeframe": timeframe,
        "rounds": rounds,
        "n_drafts": n_drafts,
        "n_pushed": 0,
        "natural_language": (
            "手动加速造策略已停用。请用 Codex 独立开发 DSL，"
            "再执行: python3 auto_trade_codex_strategy_review.py --submit PATH.json"
        ),
        "time": _now(),
    }
    # --- legacy path retained below but unreachable ---
    symbol = str(symbol or "").strip().upper()
    timeframe = str(timeframe or "").strip().lower()
    rounds = max(1, min(int(rounds or 1), 8))
    n_drafts = int(n_drafts or DRAFTS_PER_PROVIDER_MANUAL)
    seq = _manual_accelerate_seq(bump=True)
    pause = pause_auto_mode(reason="codex_manual_accelerate",
                            minutes=max(60, rounds * 45))
    # Enable budget boost for the day
    arm_manual_budget_boost()
    job = {
        "started_at": _now(),
        "symbol": symbol,
        "timeframe": timeframe,
        "rounds": rounds,
        "n_drafts": n_drafts,
        "accelerate_seq": seq,
        "accelerate_day": _today(),
        "pause": pause,
        "results": [],
    }
    _atomic(MANUAL_JOB_PATH, job)
    _wx(
        "%s 第%d次加速已启动。\n"
        "标的/周期: %s / %s · 轮数: %d · 每AI草案: %d\n"
        "预算当日上限临时翻倍(%.2f→%.2f)；自动模式暂停至 %s\n"
        "时间: %s"
        % (_today(), seq, symbol, timeframe, rounds, n_drafts,
           BASE_DAILY_BUDGET_USD,
           BASE_DAILY_BUDGET_USD * MANUAL_BUDGET_MULTIPLIER,
           pause.get("resume_after"), _now()),
        kind="factory_manual_start",
        meta={"symbol": symbol, "timeframe": timeframe, "rounds": rounds,
              "accelerate_seq": seq},
    )

    all_screened = []
    for i in range(rounds):
        context = build_factory_context(symbol=symbol, timeframe=timeframe,
                                       days=7)
        context["focus"] = {"symbol": symbol, "timeframe": timeframe}
        context["manual_round"] = i + 1
        guarded = run_quality_guarded_round(
            context, n_drafts=n_drafts, manual=True,
            source="factory_manual_r%d" % (i + 1))
        collab = guarded.get("collab") or {}
        screened = guarded.get("screened") or []
        round_row = {
            "round": i + 1,
            "n_candidates": len(collab.get("candidates") or []),
            "n_drafts_in": collab.get("n_drafts_in"),
            "n_after_revise": collab.get("n_after_revise"),
            "generate_summary": collab.get("generate_summary"),
            "pipeline": collab.get("pipeline"),
            "attempts": guarded.get("attempts"),
            "screened": screened,
            "error": collab.get("error") or guarded.get("error"),
            "budget": collab.get("budget"),
        }
        job["results"].append(round_row)
        all_screened.extend(screened)
        _atomic(MANUAL_JOB_PATH, job)
        _append_audit({"event": "manual_round", "round": i + 1,
                       "n": round_row["n_candidates"],
                       "attempts": len(guarded.get("attempts") or [])})

    pushed = sum(1 for r in all_screened if (r.get("screen") or {}).get("pushed"))
    job["finished_at"] = _now()
    job["n_pushed"] = pushed
    job["ok"] = True
    _atomic(MANUAL_JOB_PATH, job)
    try:
        ledger = _read(MANUAL_COUNT_PATH, {})
        if ledger.get("day") == _today():
            runs = list(ledger.get("runs") or [])
            runs.append({
                "seq": seq,
                "symbol": symbol,
                "timeframe": timeframe,
                "n_pushed": pushed,
                "finished_at": job["finished_at"],
            })
            ledger["runs"] = runs[-40:]
            ledger["updated_at"] = _now()
            _atomic(MANUAL_COUNT_PATH, ledger)
    except Exception:
        pass
    _atomic(STATE_PATH, {
        "ok": True, "mode": "manual_accelerate", "time": _now(),
        "job": {
            "symbol": symbol, "timeframe": timeframe, "rounds": rounds,
            "n_pushed": pushed, "accelerate_seq": seq,
        },
    })
    _wx(
        _format_manual_done_wx(seq, symbol, timeframe, rounds, pushed,
                               finished_at=job["finished_at"]),
        kind="factory_manual_done",
        meta={"symbol": symbol, "timeframe": timeframe, "pushed": pushed,
              "accelerate_seq": seq},
    )
    return job


def disable_legacy_independent_schedulers():
    """Stop/disable timers that must no longer run independently."""
    units = [
        "qiyu-strategy-hackathon.timer",
        "qiyu-strategy-breath.timer",
        "qiyu-environment-admission.timer",
        "qiyu-mass-engine.timer",
        "qiyu-microstructure-primitives.timer",
        # creator slots replaced by factory daily; keep experience ingest if any
        "qiyu-strategy-creator.timer",
        "qiyu-strategy-creator-slot1.timer",
        # deleted surfaces
        "qiyu-shadow-validator.timer",
        "qiyu-legacy-shadow-watch.timer",
        # niche map has no dedicated timer; factory owns weekly soft refresh
    ]
    stopped = []
    try:
        import subprocess
        for u in units:
            subprocess.call(["systemctl", "stop", u])
            subprocess.call(["systemctl", "disable", u])
            stopped.append(u)
        for s in ("qiyu-strategy-breath.service",
                  "qiyu-environment-admission.service",
                  "qiyu-mass-engine.service",
                  "qiyu-strategy-hackathon.service",
                  "qiyu-shadow-validator.service",
                  "qiyu-legacy-shadow-watch.service"):
            subprocess.call(["systemctl", "stop", s])
    except Exception as exc:
        return {"ok": False, "error": str(exc), "stopped": stopped}
    return {"ok": True, "stopped": stopped}


def status():
    out = {
        "ok": True,
        "time": _now(),
        "budget": budget_state(),
        "auto_paused": auto_mode_paused()[1],
        "state": _read(STATE_PATH, {}),
        "manual_job": _read(MANUAL_JOB_PATH, {}),
        "niche_week": (_read(NICHE_CACHE, {}) or {}).get("week"),
        "base_daily_budget_usd": BASE_DAILY_BUDGET_USD,
        "manual_multiplier": MANUAL_BUDGET_MULTIPLIER,
    }
    try:
        import auto_trade_strategy_dynamic_optimizer as optimizer
        out["dynamic_optimizer"] = optimizer.status()
    except Exception as exc:
        out["dynamic_optimizer_error"] = str(exc)
    return out


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Qiyu 三AI协同策略创造工厂")
    parser.add_argument("--daily", action="store_true",
                        help="运行每日协同轮次（自动模式）")
    parser.add_argument("--force-daily", action="store_true")
    parser.add_argument("--manual", action="store_true",
                        help="Codex手动加速")
    parser.add_argument("--symbol", type=str, default="BTC-USDT-SWAP")
    parser.add_argument("--timeframe", type=str, default="15m")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--drafts", type=int, default=0)
    parser.add_argument("--disable-legacy", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--refresh-niche", action="store_true")
    parser.add_argument("--optimize-daily", action="store_true",
                        help="调度策略动态优化器日轮（亦可独立定时器）")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2, default=str))
    elif args.disable_legacy:
        print(json.dumps(disable_legacy_independent_schedulers(),
                         ensure_ascii=False, indent=2))
    elif args.refresh_niche:
        print(json.dumps(niche_guidance(force=True), ensure_ascii=False,
                         indent=2, default=str)[:4000])
    elif args.optimize_daily:
        import auto_trade_strategy_dynamic_optimizer as optimizer
        print(json.dumps(optimizer.run_daily_explore(force=True),
                         ensure_ascii=False, indent=2, default=str))
    elif args.manual:
        print(json.dumps(
            run_manual_accelerate(
                args.symbol, args.timeframe, rounds=args.rounds,
                n_drafts=(args.drafts or None)),
            ensure_ascii=False, indent=2, default=str))
    else:
        print(json.dumps(
            run_daily_collaborative_round(force=args.force_daily),
            ensure_ascii=False, indent=2, default=str))
