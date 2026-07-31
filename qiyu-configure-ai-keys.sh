#!/usr/bin/env bash
set -euo pipefail
umask 077

target=/root/auto_trade/ai_ecosystem.env
read -r -s -p 'DeepSeek API Key: ' deepseek_key
printf '\n'
read -r -s -p 'Qwen/DashScope API Key: ' qwen_key
printf '\n'
read -r -s -p 'GLM (Zhipu) API Key: ' glm_key
printf '\n'
read -r -s -p 'Kimi K3 API Key（可空=保留原值/不写入）: ' kimi_key
printf '\n'

if [[ -z "$deepseek_key" || -z "$qwen_key" || -z "$glm_key" ]]; then
  printf '失败：DeepSeek/Qwen/GLM 三把密钥均为必填，原配置未修改。\n' >&2
  exit 1
fi

{
  printf '%s\n' "QIYU_DEEPSEEK_API_KEY=$deepseek_key"
  printf '%s\n' 'QIYU_DEEPSEEK_MODEL=deepseek-v4-pro'
  printf '%s\n' "QIYU_QWEN_API_KEY=$qwen_key"
  printf '%s\n' 'QIYU_QWEN_MODEL=qwen3.7-plus'
  printf '%s\n' "QIYU_GLM_API_KEY=$glm_key"
  printf '%s\n' 'QIYU_GLM_MODEL=glm-5.2'
  printf '%s\n' 'QIYU_GLM_URL=https://open.bigmodel.cn/api/paas/v4/chat/completions'
  # Kimi K3：默认可写入密钥，但 ENABLED=0，不参与三模型复核/创造
  if [[ -n "$kimi_key" ]]; then
    printf '%s\n' "QIYU_KIMI_API_KEY=$kimi_key"
  elif [[ -f "$target" ]] && grep -q '^QIYU_KIMI_API_KEY=' "$target"; then
    grep '^QIYU_KIMI_API_KEY=' "$target"
  fi
  printf '%s\n' 'QIYU_KIMI_MODEL=kimi-k3'
  printf '%s\n' 'QIYU_KIMI_URL=https://api.moonshot.ai/v1/chat/completions'
  printf '%s\n' 'QIYU_KIMI_ENABLED=0'
  printf '%s\n' 'QIYU_ECOSYSTEM_VARIANTS=6'
  printf '%s\n' 'QIYU_ECOSYSTEM_BACKTEST_START="2026-01-01 00:00:00"'
} > "${target}.tmp"
chmod 600 "${target}.tmp"
mv -f "${target}.tmp" "$target"
unset deepseek_key qwen_key glm_key kimi_key
printf '完成：密钥已写入权限为600的配置文件；Kimi 为待机（ENABLED=0）。\n'
set -a
source "$target"
set +a
python3 /root/auto_trade_strategy_ecosystem.py --status
