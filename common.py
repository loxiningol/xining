import os, time, datetime, json, requests, hmac, base64, hashlib

def _load_local_env():
    """Optional gitignored local_secrets.env next to this file."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_secrets.env")
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


_load_local_env()

# Credentials from environment / local_secrets.env — never hardcode in git.
OKX_API_KEY = os.environ.get("OKX_API_KEY", "").strip()
OKX_SECRET = os.environ.get("OKX_SECRET", "").strip()
OKX_PASSPHRASE = os.environ.get("OKX_PASSPHRASE", "").strip()
WX_TOKEN = os.environ.get("WX_TOKEN", "").strip()
WX_UID = os.environ.get("WX_UID", "").strip()

def send_wx(msg):
    try:
        _msg = str(msg or "")
        # Hard block: never push triple-friction tip spam.
        if ("三倍摩擦风险提示" in _msg) or ("三倍摩擦压力提示（非淘汰）" in _msg):
            return
        try:
            import auto_trade_strategy_titles as titles
            msg = titles.rewrite_strategy_keys_in_text(msg)
        except Exception:
            pass
        requests.post("https://wxpusher.zjiecode.com/api/send/message", 
                      json={"appToken": WX_TOKEN, "content": msg, "summary": "策略信号通知", "contentType": 1, "uids": [WX_UID]}, 
                      timeout=5)
    except:
        pass

def okx_req(path, timeout=10):
    t = datetime.datetime.utcnow().isoformat(timespec='milliseconds')+'Z'
    m = 'GET' + path
    sg = base64.b64encode(hmac.new(OKX_SECRET.encode(), m.encode(), hashlib.sha256).digest()).decode()
    h = {"OK-ACCESS-KEY": OKX_API_KEY, "OK-ACCESS-SIGN": sg, "OK-ACCESS-TIMESTAMP": t, "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE}
    for _ in range(3):
        try:
            resp = requests.get("https://www.okx.com" + path, headers=h, timeout=timeout)
            if resp.text and resp.text.strip():
                return resp.json()
        except:
            pass
        time.sleep(2)
    return {"code": "-1", "msg": "网络请求重试失败"}

def get_klines(limit=200, instId="BTC-USDT"):
    path = f"/api/v5/market/history-candles?instId={instId}&bar=1H&limit={limit}"
    d = okx_req(path)
    if d.get("code") != "0": return [], [], [], [], []
    candles = d["data"]
    candles.reverse()
    return ([float(c[1]) for c in candles], [float(c[4]) for c in candles], 
            [float(c[2]) for c in candles], [float(c[3]) for c in candles], [int(c[0]) for c in candles])

def get_klines_15min(limit=200, instId="BTC-USDT"):
    path = f"/api/v5/market/history-candles?instId={instId}&bar=15m&limit={limit}"
    d = okx_req(path)
    if d.get("code") != "0": return [], [], [], [], []
    candles = d["data"]
    candles.reverse()
    return ([float(c[1]) for c in candles], [float(c[4]) for c in candles], 
            [float(c[2]) for c in candles], [float(c[3]) for c in candles], [int(c[0]) for c in candles])

def ema(data, n):
    if len(data) < n: return data[-1] if data else 0
    k = 2 / (n + 1)
    r = sum(data[:n]) / n
    for x in data[n:]: r = x * k + r * (1 - k)
    return r

def kdj(highs, lows, closes):
    if len(closes) < 24: return 50, 50, 50
    k, d = 50, 50
    for i in range(24, len(closes)):
        hh = max(highs[i-23:i+1])
        ll = min(lows[i-23:i+1])
        rsv = 50 if hh == ll else (closes[i] - ll) * 100 / (hh - ll)
        k = (rsv + 2 * k) / 3
        d = (k + 2 * d) / 3
    return k, d, 3 * k - 2 * d

def cci(highs, lows, closes, p=62):
    if len(closes) < p: return 0
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    ma_tp = sum(tp[-p:]) / p
    md = sum(abs(t - ma_tp) for t in tp[-p:]) / p
    return (tp[-1] - ma_tp) / (0.015 * md) if md else 0

def macd(data):
    if len(data) < 26: return 0, 0, 0
    diff = ema(data, 12) - ema(data, 26)
    diffs = [ema(data[:i+1], 12) - ema(data[:i+1], 26) for i in range(len(data))]
    dea = ema(diffs, 9)
    return diff, dea, 2 * (diff - dea)

def is_pid_alive(pid_file):
    try:
        with open(pid_file) as f: pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except:
        return False

def generate_report(manual=False):
    from signal_mgr import get_active
    now = datetime.datetime.now()
    title = now.strftime("%Y-%m-%d %H:%M") + " 手动盘面清查报告" if manual else "早安栖语系统状态日报"
    lines = [f"=== {title} ==="]
    for name, pid in [("正式做多监控", "/root/cci_75_100.pid"), ("正式做空监控", "/root/cci75_110_short.pid")]:
        lines.append(f"📡 {name}进程状态: {'运行中 (🟢)' if is_pid_alive(pid) else '异常停止 (🔴)'}")
    signals = get_active()
    lines.append(f"📊 当前全网激活持仓信号数: {len(signals)}")
    for s in signals:
        lines.append(f"  -> 策略: {s['name']} | 触发时间: {s['trigger_time']} | 入场开仓价: {s['price']}")
    return "\n".join(lines)
