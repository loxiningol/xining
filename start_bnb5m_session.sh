#!/bin/bash
set -e
# Kill only real interpreter PIDs (avoid matching this launcher argv).
ps -eo pid,cmd | awk '/[p]ython3 -u \/root\/frost3_bnb5m_session_run.py/{print $1}' | while read p; do
  kill -9 "$p" 2>/dev/null || true
done
sleep 1
python3 -m py_compile /root/frost3_bnb5m_session_run.py
echo COMPILE_OK
# smoke validate
python3 - <<'PY'
import sys
sys.path.insert(0, "/root")
import frost3_bnb5m_session_run as m
import auto_trade_strategy_dsl as dsl
m.install_session_proxies()
book = m.make_book(m.fallback_params({})["hypothesis"], 1)
dsl.validate_strategy(book["dsl"])
print("VALIDATE_OK", book["dsl"]["key"])
PY
export BNB5M_FORCE_FALLBACK=1
export BNB5M_MAX_BARS="${BNB5M_MAX_BARS:-9000}"
# Precompute compact frame (avoids contended factory _frame / parquet rebuild)
BNB5M_MAX_BARS="$BNB5M_MAX_BARS" python3 /root/bnb5m_precompute_frame.py
: > /root/auto_trade/dual_engine/frost3_bnb5m_session_run.log
# remove stale quick artifact so progress is unambiguous
rm -f /root/auto_trade/dual_engine/frost3_bnb5m_session_quick_try*.json \
      /root/auto_trade/dual_engine/frost3_bnb5m_session_full_try*.json \
      /root/auto_trade/dual_engine/frost3_bnb5m_session_end_report.json \
      /root/auto_trade/dual_engine/frost3_bnb5m_session_parent_summary_zh.json
nohup nice -n 5 env BNB5M_FORCE_FALLBACK=1 BNB5M_MAX_BARS="$BNB5M_MAX_BARS" \
  python3 -u /root/frost3_bnb5m_session_run.py \
  >> /root/auto_trade/dual_engine/frost3_bnb5m_session_run.log 2>&1 &
echo PID:$! MAX_BARS:$BNB5M_MAX_BARS
sleep 45
tail -80 /root/auto_trade/dual_engine/frost3_bnb5m_session_run.log
ls -lt /root/auto_trade/dual_engine/frost3_bnb5m_session_*.json | head -25
ps -eo pid,etime,state,%cpu,%mem,cmd | awk '/[p]ython3 -u \/root\/frost3_bnb5m_session_run.py/{print}'
