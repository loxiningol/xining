-- workflow_v2 forward migration 001
-- Safe: only creates new tables; does not alter live trading tables.

CREATE TABLE IF NOT EXISTS wf_task_meta (
  task_id TEXT PRIMARY KEY,
  strategy_id TEXT,
  exploration_mode TEXT NOT NULL,
  workflow_version TEXT NOT NULL,
  migration_version TEXT NOT NULL,
  code_version TEXT,
  data_version TEXT,
  backtest_version TEXT,
  status TEXT,
  symbol TEXT,
  timeframe TEXT,
  created_at TEXT,
  updated_at TEXT,
  payload_json TEXT
);

CREATE TABLE IF NOT EXISTS wf_mechanism_statement (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  strategy_id TEXT,
  mechanism_id TEXT,
  model_call_id TEXT,
  code_version TEXT,
  data_version TEXT,
  backtest_version TEXT,
  statement_json TEXT NOT NULL,
  content_hash TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wf_mechanism_fingerprint (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  strategy_id TEXT,
  mechanism_id TEXT,
  family_hash TEXT,
  fingerprint_hash TEXT,
  fingerprint_json TEXT NOT NULL,
  active INTEGER DEFAULT 1,
  code_version TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wf_condition_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  strategy_id TEXT,
  model_call_id TEXT,
  audit_json TEXT NOT NULL,
  rejected INTEGER,
  code_version TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wf_mechanism_fidelity_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  strategy_id TEXT,
  report_json TEXT NOT NULL,
  passed INTEGER,
  code_version TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wf_necessity_test (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  strategy_id TEXT,
  report_json TEXT NOT NULL,
  passed INTEGER,
  code_version TEXT,
  backtest_version TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wf_param_stability_test (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  strategy_id TEXT,
  report_json TEXT NOT NULL,
  passed INTEGER,
  high_overfit INTEGER,
  code_version TEXT,
  backtest_version TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wf_condition_contribution_test (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  strategy_id TEXT,
  report_json TEXT NOT NULL,
  code_version TEXT,
  backtest_version TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wf_mechanism_drift_report (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  strategy_id TEXT,
  repair_round INTEGER,
  report_json TEXT NOT NULL,
  drift_level TEXT,
  code_version TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wf_multidimensional_review (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  strategy_id TEXT,
  model_call_id TEXT,
  report_json TEXT NOT NULL,
  approved INTEGER,
  fatal_blocks TEXT,
  code_version TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wf_structured_failure_archive (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  strategy_id TEXT,
  mechanism_id TEXT,
  failure_level TEXT NOT NULL,
  archive_json TEXT NOT NULL,
  exclusion_rule_json TEXT,
  code_version TEXT,
  data_version TEXT,
  backtest_version TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wf_model_call_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  call_id TEXT UNIQUE,
  task_id TEXT,
  provider TEXT,
  purpose TEXT,
  request_hash TEXT,
  ok INTEGER,
  error TEXT,
  code_version TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS wf_migration_ledger (
  migration_version TEXT PRIMARY KEY,
  applied_at TEXT,
  direction TEXT,
  note TEXT
);

CREATE INDEX IF NOT EXISTS idx_wf_fp_family ON wf_mechanism_fingerprint(family_hash);
CREATE INDEX IF NOT EXISTS idx_wf_fail_level ON wf_structured_failure_archive(failure_level);
CREATE INDEX IF NOT EXISTS idx_wf_task_mode ON wf_task_meta(exploration_mode);
