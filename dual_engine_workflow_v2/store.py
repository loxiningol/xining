# -*- coding: utf-8 -*-
"""SQLite persistence for workflow v2 entities."""
from __future__ import print_function

import json
import os
import sqlite3
from pathlib import Path

from .config import (
    AUTO_DIR,
    CODE_VERSION,
    DATA_VERSION,
    BACKTEST_VERSION,
    MIGRATION_VERSION,
    WORKFLOW_VERSION,
    WF_DIR,
    load_flags,
    _now,
    ensure_dirs,
)

DB_PATH = AUTO_DIR / "strategy_ecosystem.db"
MIG_DIR = Path(__file__).resolve().parent / "migrations"


def _conn():
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=60000")
    except Exception:
        pass
    return conn


def migrate_forward(retries=12):
    ensure_dirs()
    sql_path = MIG_DIR / "001_forward.sql"
    sql = sql_path.read_text(encoding="utf-8")
    last_err = None
    for attempt in range(int(retries)):
        conn = _conn()
        try:
            conn.executescript(sql)
            conn.execute(
                "INSERT OR REPLACE INTO wf_migration_ledger(migration_version,applied_at,direction,note) VALUES(?,?,?,?)",
                (MIGRATION_VERSION, _now(), "forward", "workflow_v2_001"),
            )
            conn.commit()
            last_err = None
            break
        except sqlite3.OperationalError as exc:
            last_err = exc
            import time as _t
            _t.sleep(0.75 * (attempt + 1))
        finally:
            conn.close()
    if last_err is not None:
        raise last_err
    # also copy migrations into dual_engine/workflow_v2 for remote ops
    dest = WF_DIR / "migrations"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("001_forward.sql", "001_rollback.sql"):
        src = MIG_DIR / name
        if src.exists():
            (dest / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    flags = load_flags()
    from .config import save_flags
    save_flags({"migration_version": MIGRATION_VERSION, "upgrade_status": flags.get("upgrade_status") or "migrated"})
    return {"ok": True, "migration_version": MIGRATION_VERSION}


def migrate_rollback():
    sql_path = MIG_DIR / "001_rollback.sql"
    sql = sql_path.read_text(encoding="utf-8")
    conn = _conn()
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()
    from .config import save_flags
    save_flags({"migration_version": None, "creation_entry": "legacy", "workflow_version": "v1",
                "upgrade_status": "rolled_back_db"})
    return {"ok": True, "rolled_back": MIGRATION_VERSION}


def _insert(table, cols, values):
    conn = _conn()
    try:
        placeholders = ",".join(["?"] * len(cols))
        conn.execute(
            "INSERT INTO %s(%s) VALUES(%s)" % (table, ",".join(cols), placeholders),
            values,
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        conn.close()


def save_task_meta(task):
    cols = [
        "task_id", "strategy_id", "exploration_mode", "workflow_version",
        "migration_version", "code_version", "data_version", "backtest_version",
        "status", "symbol", "timeframe", "created_at", "updated_at", "payload_json",
    ]
    focus = task.get("focus") or {}
    values = [
        task.get("id"),
        task.get("strategy_id"),
        task.get("exploration_mode"),
        WORKFLOW_VERSION,
        MIGRATION_VERSION,
        CODE_VERSION,
        DATA_VERSION,
        BACKTEST_VERSION,
        task.get("stage"),
        focus.get("symbol"),
        focus.get("timeframe"),
        task.get("created_at") or _now(),
        _now(),
        json.dumps({
            "candidate": task.get("candidate"),
            "failure_level": (task.get("failure_archive") or {}).get("failure_level"),
            "drift": task.get("drift"),
        }, ensure_ascii=False),
    ]
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO wf_task_meta(%s) VALUES(%s)" % (
                ",".join(cols), ",".join(["?"] * len(cols))),
            values,
        )
        conn.commit()
    finally:
        conn.close()


def save_mechanism_statement(task_id, strategy_id, mechanism_id, statement, model_call_id=None):
    from .protocol import content_hash
    _insert(
        "wf_mechanism_statement",
        ["task_id", "strategy_id", "mechanism_id", "model_call_id", "code_version",
         "data_version", "backtest_version", "statement_json", "content_hash", "created_at"],
        [task_id, strategy_id, mechanism_id, model_call_id, CODE_VERSION, DATA_VERSION,
         BACKTEST_VERSION, json.dumps(statement, ensure_ascii=False),
         content_hash(statement), _now()],
    )


def save_fingerprint(task_id, strategy_id, mechanism_id, fp):
    _insert(
        "wf_mechanism_fingerprint",
        ["task_id", "strategy_id", "mechanism_id", "family_hash", "fingerprint_hash",
         "fingerprint_json", "active", "code_version", "created_at"],
        [task_id, strategy_id, mechanism_id, fp.get("family_hash"), fp.get("fingerprint_hash"),
         json.dumps(fp, ensure_ascii=False), 1, CODE_VERSION, _now()],
    )


def list_fingerprints(limit=500):
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT fingerprint_json, family_hash, fingerprint_hash, strategy_id, mechanism_id "
            "FROM wf_mechanism_fingerprint WHERE active=1 ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            fp = json.loads(r["fingerprint_json"])
            out.append({
                "fingerprint": fp,
                "family_hash": r["family_hash"],
                "fingerprint_hash": r["fingerprint_hash"],
                "strategy_id": r["strategy_id"],
                "mechanism_id": r["mechanism_id"],
            })
        return out
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def save_condition_audit(task_id, strategy_id, report, model_call_id=None):
    _insert(
        "wf_condition_audit",
        ["task_id", "strategy_id", "model_call_id", "audit_json", "rejected",
         "code_version", "created_at"],
        [task_id, strategy_id, model_call_id, json.dumps(report, ensure_ascii=False),
         1 if report.get("reject") else 0, CODE_VERSION, _now()],
    )
    _insert(
        "wf_mechanism_fidelity_audit",
        ["task_id", "strategy_id", "report_json", "passed", "code_version", "created_at"],
        [task_id, strategy_id, json.dumps(report, ensure_ascii=False),
         1 if report.get("pass") else 0, CODE_VERSION, _now()],
    )


def save_abc_tests(task_id, strategy_id, abc):
    nec = abc.get("necessity") or {}
    stab = abc.get("parameter_stability") or {}
    contrib = abc.get("contribution") or {}
    _insert("wf_necessity_test",
            ["task_id", "strategy_id", "report_json", "passed", "code_version",
             "backtest_version", "created_at"],
            [task_id, strategy_id, json.dumps(nec, ensure_ascii=False),
             1 if nec.get("pass") else 0, CODE_VERSION, BACKTEST_VERSION, _now()])
    _insert("wf_param_stability_test",
            ["task_id", "strategy_id", "report_json", "passed", "high_overfit",
             "code_version", "backtest_version", "created_at"],
            [task_id, strategy_id, json.dumps(stab, ensure_ascii=False),
             1 if stab.get("pass") else 0, 1 if stab.get("high_overfit_risk") else 0,
             CODE_VERSION, BACKTEST_VERSION, _now()])
    _insert("wf_condition_contribution_test",
            ["task_id", "strategy_id", "report_json", "code_version",
             "backtest_version", "created_at"],
            [task_id, strategy_id, json.dumps(contrib, ensure_ascii=False),
             CODE_VERSION, BACKTEST_VERSION, _now()])


def save_drift(task_id, strategy_id, repair_round, drift):
    _insert(
        "wf_mechanism_drift_report",
        ["task_id", "strategy_id", "repair_round", "report_json", "drift_level",
         "code_version", "created_at"],
        [task_id, strategy_id, repair_round, json.dumps(drift, ensure_ascii=False),
         (drift or {}).get("drift"), CODE_VERSION, _now()],
    )


def save_multidimensional(task_id, strategy_id, report, model_call_id=None):
    _insert(
        "wf_multidimensional_review",
        ["task_id", "strategy_id", "model_call_id", "report_json", "approved",
         "fatal_blocks", "code_version", "created_at"],
        [task_id, strategy_id, model_call_id, json.dumps(report, ensure_ascii=False),
         1 if report.get("approved") else 0,
         json.dumps(report.get("fatal_blocks") or [], ensure_ascii=False),
         CODE_VERSION, _now()],
    )


def save_failure_archive(archive):
    _insert(
        "wf_structured_failure_archive",
        ["task_id", "strategy_id", "mechanism_id", "failure_level", "archive_json",
         "exclusion_rule_json", "code_version", "data_version", "backtest_version", "created_at"],
        [archive.get("task_id"), archive.get("strategy_id"), archive.get("mechanism_id"),
         archive.get("failure_level"), json.dumps(archive, ensure_ascii=False),
         json.dumps(archive.get("reusable_exclusion_rule") or {}, ensure_ascii=False),
         archive.get("code_version") or CODE_VERSION,
         archive.get("data_version") or DATA_VERSION,
         archive.get("backtest_version") or BACKTEST_VERSION, _now()],
    )
    # also append freezer-compatible jsonl
    ensure_dirs()
    freezer = WF_DIR / "failure_archives.jsonl"
    with freezer.open("a", encoding="utf-8") as f:
        f.write(json.dumps(archive, ensure_ascii=False) + "\n")


def list_exclusion_rules(limit=200):
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT exclusion_rule_json, failure_level FROM wf_structured_failure_archive "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            try:
                out.append({
                    "failure_level": r["failure_level"],
                    "rule": json.loads(r["exclusion_rule_json"] or "{}"),
                })
            except Exception:
                pass
        return out
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def latest_task_artifacts(task_id):
    """Bundle latest persisted reports for UI column 4."""
    conn = _conn()
    out = {"task_id": task_id}
    try:
        def one(sql, args=()):
            try:
                r = conn.execute(sql, args).fetchone()
                return dict(r) if r else None
            except sqlite3.OperationalError:
                return None

        out["task_meta"] = one("SELECT * FROM wf_task_meta WHERE task_id=?", (task_id,))
        for key, table in (
            ("mechanism_statement", "wf_mechanism_statement"),
            ("fingerprint", "wf_mechanism_fingerprint"),
            ("condition_audit", "wf_condition_audit"),
            ("necessity", "wf_necessity_test"),
            ("stability", "wf_param_stability_test"),
            ("contribution", "wf_condition_contribution_test"),
            ("drift", "wf_mechanism_drift_report"),
            ("review4d", "wf_multidimensional_review"),
            ("failure", "wf_structured_failure_archive"),
        ):
            row = one("SELECT * FROM %s WHERE task_id=? ORDER BY id DESC LIMIT 1" % table, (task_id,))
            if row:
                # parse json fields
                parsed = dict(row)
                for fk in list(parsed.keys()):
                    if fk.endswith("_json") or fk in ("statement_json", "audit_json", "report_json",
                                                       "archive_json", "fingerprint_json", "payload_json"):
                        try:
                            parsed[fk.replace("_json", "") if fk.endswith("_json") else fk] = json.loads(parsed[fk])
                        except Exception:
                            pass
                out[key] = parsed
        return out
    finally:
        conn.close()


def workflow_status_payload():
    flags = load_flags()
    recent_failures = []
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT task_id, strategy_id, failure_level, created_at FROM wf_structured_failure_archive "
            "ORDER BY id DESC LIMIT 8"
        ).fetchall()
        recent_failures = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
    return {
        "workflow_version": flags.get("workflow_version"),
        "creation_entry": flags.get("creation_entry"),
        "migration_version": flags.get("migration_version"),
        "upgrade_status": flags.get("upgrade_status"),
        "acceptance": flags.get("acceptance"),
        "switched_at": flags.get("switched_at"),
        "pause": flags.get("pause"),
        "code_version": CODE_VERSION,
        "recent_failures": recent_failures,
        "flow": "new" if flags.get("creation_entry") == "v2" else "old",
    }
