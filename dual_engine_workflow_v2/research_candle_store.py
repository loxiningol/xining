# -*- coding: utf-8 -*-
"""Research candle object store (S3 / Cloudflare R2 compatible).

Purpose: long OKX history for strategy *creation* — separate from live
formal_*_candles_cache.json rolling short windows.

Object layout:
  okx/candles/{instId}/{bar}/v1/manifest.json
  okx/candles/{instId}/{bar}/v1/chunks/{YYYYMM}.json.gz

Env (prefer R2_* ; S3_* also accepted):
  QIYU_R2_ACCOUNT_ID / QIYU_R2_ENDPOINT
  QIYU_R2_ACCESS_KEY_ID / QIYU_R2_SECRET_ACCESS_KEY
  QIYU_R2_BUCKET
  QIYU_RESEARCH_CANDLES_MODE = r2|s3|local|off   (default: auto)
  QIYU_RESEARCH_LOCAL_ROOT   = /root/auto_trade/research_candle_store
  QIYU_RESEARCH_CACHE_ROOT   = /root/auto_trade/research_candle_cache

Does NOT touch formal live caches. Does NOT mount trading.
"""
from __future__ import print_function

import gzip
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.utils import formatdate
from pathlib import Path


SCHEMA_MANIFEST = "qiyu_research_candle_manifest_v1"
SCHEMA_CHUNK = "qiyu_research_candle_chunk_v1"


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _env(*names, default=""):
    for n in names:
        v = os.environ.get(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return default


def mode():
    m = _env("QIYU_RESEARCH_CANDLES_MODE", default="auto").lower()
    if m in ("0", "off", "false", "no", "disabled"):
        return "off"
    if m in ("r2", "s3", "local", "auto"):
        return m
    return "auto"


def local_root():
    return Path(_env("QIYU_RESEARCH_LOCAL_ROOT", default="/root/auto_trade/research_candle_store"))


def cache_root():
    return Path(_env("QIYU_RESEARCH_CACHE_ROOT", default="/root/auto_trade/research_candle_cache"))


def _r2_endpoint():
    ep = _env("QIYU_R2_ENDPOINT", "QIYU_S3_ENDPOINT")
    if ep:
        return ep.rstrip("/")
    acct = _env("QIYU_R2_ACCOUNT_ID")
    if acct:
        return "https://%s.r2.cloudflarestorage.com" % acct
    return ""


def credentials():
    return {
        "access_key": _env("QIYU_R2_ACCESS_KEY_ID", "QIYU_S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
        "secret_key": _env("QIYU_R2_SECRET_ACCESS_KEY", "QIYU_S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"),
        "bucket": _env("QIYU_R2_BUCKET", "QIYU_S3_BUCKET", default="qiyu-research-candles"),
        "region": _env("QIYU_R2_REGION", "QIYU_S3_REGION", "AWS_DEFAULT_REGION", default="auto"),
        "endpoint": _r2_endpoint(),
    }


def probe():
    m = mode()
    cred = credentials()
    out = {
        "ok": False,
        "mode": m,
        "provider": "s3_compatible",
        "bucket": cred["bucket"],
        "endpoint_set": bool(cred["endpoint"]),
        "keys_set": bool(cred["access_key"] and cred["secret_key"]),
        "local_root": str(local_root()),
        "cache_root": str(cache_root()),
        "at": _now(),
    }
    resolved = resolve_backend()
    out["resolved_backend"] = resolved
    if resolved == "off":
        out["ok"] = False
        out["error"] = "research_candles_disabled"
        return out
    if resolved == "local":
        local_root().mkdir(parents=True, exist_ok=True)
        out["ok"] = True
        out["note_zh"] = "本地镜像后端（无云端密钥时用于开发/过渡）。"
        return out
    if not cred["endpoint"] or not cred["access_key"] or not cred["secret_key"]:
        out["error"] = "missing_r2_or_s3_credentials"
        out["hint_zh"] = (
            "请在 ai_ecosystem.env 配置 QIYU_R2_ACCOUNT_ID / ACCESS_KEY_ID / "
            "SECRET_ACCESS_KEY / BUCKET，或设 QIYU_RESEARCH_CANDLES_MODE=local"
        )
        return out
    out["ok"] = True
    out["note_zh"] = "S3/R2 凭证已配置；创造阶段可按需拉取长历史。"
    return out


def resolve_backend():
    m = mode()
    if m == "off":
        return "off"
    if m == "local":
        return "local"
    cred = credentials()
    has = bool(cred["endpoint"] and cred["access_key"] and cred["secret_key"])
    if m in ("r2", "s3"):
        return "s3" if has else "off"
    # auto
    if has:
        return "s3"
    return "local"


def object_prefix(inst_id, bar):
    inst = str(inst_id).upper()
    bar = _norm_bar(bar)
    return "okx/candles/%s/%s/v1" % (inst, bar)


def manifest_key(inst_id, bar):
    return "%s/manifest.json" % object_prefix(inst_id, bar)


def chunk_key(inst_id, bar, yyyymm):
    return "%s/chunks/%s.json.gz" % (object_prefix(inst_id, bar), yyyymm)


def _norm_bar(bar):
    b = str(bar or "5m").strip()
    if b.upper() == "1H":
        return "1H"
    return b.lower()


# ---------- AWS SigV4 (minimal Put/Get) ----------

def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sigv4_headers(method, url, payload_bytes, access_key, secret_key, region, service="s3"):
    """Return headers for SigV4 signed request."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc
    path = parsed.path or "/"
    query = parsed.query
    t = time.gmtime()
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", t)
    datestamp = time.strftime("%Y%m%d", t)
    payload_hash = hashlib.sha256(payload_bytes or b"").hexdigest()
    canonical_headers = "host:%s\nx-amz-content-sha256:%s\nx-amz-date:%s\n" % (
        host, payload_hash, amz_date,
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join([
        method.upper(),
        path,
        query,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])
    credential_scope = "%s/%s/%s/aws4_request" % (datestamp, region if region != "auto" else "auto", service)
    # R2 accepts region "auto"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])
    k_date = _sign(("AWS4" + secret_key).encode("utf-8"), datestamp)
    k_region = _sign(k_date, region if region != "auto" else "auto")
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    auth = (
        "AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
        % (access_key, credential_scope, signed_headers, signature)
    )
    return {
        "Host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Authorization": auth,
        "Content-Type": "application/octet-stream",
    }


class S3CompatibleStore(object):
    def __init__(self, endpoint=None, bucket=None, access_key=None, secret_key=None, region=None):
        cred = credentials()
        self.endpoint = (endpoint or cred["endpoint"]).rstrip("/")
        self.bucket = bucket or cred["bucket"]
        self.access_key = access_key or cred["access_key"]
        self.secret_key = secret_key or cred["secret_key"]
        self.region = region or cred["region"] or "auto"

    def _url(self, key):
        # path-style: https://endpoint/bucket/key
        return "%s/%s/%s" % (self.endpoint, self.bucket, key.lstrip("/"))

    def put_bytes(self, key, data, content_type="application/octet-stream", timeout=60):
        body = data if isinstance(data, (bytes, bytearray)) else bytes(data)
        url = self._url(key)
        headers = _sigv4_headers(
            "PUT", url, body, self.access_key, self.secret_key, self.region,
        )
        headers["Content-Type"] = content_type
        headers["Content-Length"] = str(len(body))
        req = urllib.request.Request(url, data=body, headers=headers, method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
                return {"ok": True, "status": getattr(resp, "status", 200), "key": key}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                detail = str(exc)
            return {"ok": False, "error": "http_%s" % exc.code, "detail": detail, "key": key}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:240], "key": key}

    def get_bytes(self, key, timeout=60):
        url = self._url(key)
        headers = _sigv4_headers(
            "GET", url, b"", self.access_key, self.secret_key, self.region,
        )
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
                return {"ok": True, "data": resp.read(), "key": key}
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {"ok": False, "error": "not_found", "key": key}
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                detail = str(exc)
            return {"ok": False, "error": "http_%s" % exc.code, "detail": detail, "key": key}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:240], "key": key}


class LocalMirrorStore(object):
    """Filesystem mirror with the same key layout (dev / no-cloud fallback)."""

    def __init__(self, root=None):
        self.root = Path(root or local_root())
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key):
        return self.root / key

    def put_bytes(self, key, data, content_type="application/octet-stream", timeout=60):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = data if isinstance(data, (bytes, bytearray)) else bytes(data)
        path.write_bytes(body)
        return {"ok": True, "status": 200, "key": key, "path": str(path)}

    def get_bytes(self, key, timeout=60):
        path = self._path(key)
        if not path.exists():
            return {"ok": False, "error": "not_found", "key": key}
        return {"ok": True, "data": path.read_bytes(), "key": key, "path": str(path)}


def get_store():
    backend = resolve_backend()
    if backend == "off":
        return None, "off"
    if backend == "local":
        return LocalMirrorStore(), "local"
    return S3CompatibleStore(), "s3"


def _gzip_json(obj):
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw)


def _gunzip_json(data):
    raw = gzip.decompress(data)
    return json.loads(raw.decode("utf-8"))


def yyyymm_of_ts(ts_ms):
    t = int(ts_ms)
    if t < 1e12:
        t *= 1000
    return datetime.utcfromtimestamp(t / 1000.0).strftime("%Y%m")


def put_chunk(inst_id, bar, yyyymm, candles, store=None):
    store = store or get_store()[0]
    if store is None:
        return {"ok": False, "error": "store_unavailable"}
    payload = {
        "schema": SCHEMA_CHUNK,
        "instId": str(inst_id).upper(),
        "bar": _norm_bar(bar),
        "yyyymm": str(yyyymm),
        "n": len(candles or []),
        "candles": candles or [],
        "written_at": _now(),
    }
    key = chunk_key(inst_id, bar, yyyymm)
    return store.put_bytes(key, _gzip_json(payload), content_type="application/gzip")


def get_chunk(inst_id, bar, yyyymm, store=None):
    store = store or get_store()[0]
    if store is None:
        return {"ok": False, "error": "store_unavailable"}
    key = chunk_key(inst_id, bar, yyyymm)
    res = store.get_bytes(key)
    if not res.get("ok"):
        return res
    try:
        payload = _gunzip_json(res["data"])
        return {"ok": True, "key": key, "payload": payload}
    except Exception as exc:
        return {"ok": False, "error": "decode_fail:%s" % exc, "key": key}


def load_manifest(inst_id, bar, store=None):
    store = store or get_store()[0]
    if store is None:
        return {"ok": False, "error": "store_unavailable"}
    key = manifest_key(inst_id, bar)
    res = store.get_bytes(key)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "manifest_missing", "key": key}
    try:
        man = json.loads(res["data"].decode("utf-8"))
        return {"ok": True, "manifest": man, "key": key}
    except Exception as exc:
        return {"ok": False, "error": "manifest_decode:%s" % exc}


def save_manifest(inst_id, bar, manifest, store=None):
    store = store or get_store()[0]
    if store is None:
        return {"ok": False, "error": "store_unavailable"}
    man = dict(manifest or {})
    man["schema"] = SCHEMA_MANIFEST
    man["instId"] = str(inst_id).upper()
    man["bar"] = _norm_bar(bar)
    man["updated_at"] = _now()
    key = manifest_key(inst_id, bar)
    body = json.dumps(man, ensure_ascii=False, indent=2).encode("utf-8")
    return store.put_bytes(key, body, content_type="application/json")


def _months_between(start_ms, end_ms):
    if start_ms > end_ms:
        start_ms, end_ms = end_ms, start_ms
    cur = datetime.utcfromtimestamp((start_ms if start_ms > 1e12 else start_ms * 1000) / 1000.0)
    end = datetime.utcfromtimestamp((end_ms if end_ms > 1e12 else end_ms * 1000) / 1000.0)
    out = []
    y, m = cur.year, cur.month
    while (y, m) <= (end.year, end.month):
        out.append("%04d%02d" % (y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def load_candles_range(inst_id, bar, start_ms=None, end_ms=None, max_bars=None):
    """Pull month chunks from R2/local for [start_ms, end_ms], merge, sort.

    Falls back with ok=False if store/manifest missing — caller may use formal short cache.
    """
    store, backend = get_store()
    if store is None:
        return {"ok": False, "error": "store_off", "candles": [], "backend": backend}
    man_res = load_manifest(inst_id, bar, store=store)
    if not man_res.get("ok"):
        return {
            "ok": False,
            "error": man_res.get("error") or "no_manifest",
            "candles": [],
            "backend": backend,
            "hint_zh": "请先运行 scripts/research_candles_backfill_r2.py 回填长历史。",
        }
    man = man_res["manifest"]
    chunks_meta = man.get("chunks") or []
    if start_ms is None and chunks_meta:
        start_ms = min(int(c.get("start_ts") or 0) for c in chunks_meta)
    if end_ms is None and chunks_meta:
        end_ms = max(int(c.get("end_ts") or 0) for c in chunks_meta)
    if start_ms is None or end_ms is None:
        return {"ok": False, "error": "empty_manifest", "candles": [], "backend": backend}

    months = _months_between(start_ms, end_ms)
    # Prefer declared chunk list if present
    if chunks_meta:
        months = sorted(set(
            [str(c.get("yyyymm")) for c in chunks_meta if c.get("yyyymm")]
            + months
        ))

    merged = {}
    loaded = []
    for ym in months:
        # disk cache first
        cache_path = cache_root() / object_prefix(inst_id, bar) / ("chunks_%s.json.gz" % ym)
        payload = None
        if cache_path.exists():
            try:
                payload = _gunzip_json(cache_path.read_bytes())
                loaded.append({"yyyymm": ym, "source": "disk_cache"})
            except Exception:
                payload = None
        if payload is None:
            got = get_chunk(inst_id, bar, ym, store=store)
            if not got.get("ok"):
                continue
            payload = got["payload"]
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                cache_path.write_bytes(_gzip_json(payload))
            except Exception:
                pass
            loaded.append({"yyyymm": ym, "source": backend})
        for c in payload.get("candles") or []:
            try:
                ts = int(c["ts"])
            except Exception:
                continue
            if ts < int(start_ms) or ts > int(end_ms):
                continue
            merged[ts] = {
                "ts": ts,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            }
    rows = [merged[k] for k in sorted(merged)]
    if max_bars is not None and len(rows) > int(max_bars):
        rows = rows[-int(max_bars):]
    return {
        "ok": bool(rows),
        "schema": "qiyu_research_candles_range_v1",
        "instId": str(inst_id).upper(),
        "bar": _norm_bar(bar),
        "backend": backend,
        "n": len(rows),
        "candles": rows,
        "chunks_loaded": loaded,
        "start_ts": rows[0]["ts"] if rows else None,
        "end_ts": rows[-1]["ts"] if rows else None,
        "at": _now(),
    }


def load_for_creation(symbol, timeframe, lookback_days=400, max_bars=50000):
    """Convenience for creation blueprint: last N days from research store."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - int(lookback_days) * 86400 * 1000
    return load_candles_range(
        symbol, timeframe, start_ms=start_ms, end_ms=end_ms, max_bars=max_bars,
    )
