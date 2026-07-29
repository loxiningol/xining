-- workflow_v2 rollback migration 001
-- Drops only v2 tables. Does NOT touch live trading / formal / rating tables.

DROP TABLE IF EXISTS wf_model_call_log;
DROP TABLE IF EXISTS wf_structured_failure_archive;
DROP TABLE IF EXISTS wf_multidimensional_review;
DROP TABLE IF EXISTS wf_mechanism_drift_report;
DROP TABLE IF EXISTS wf_condition_contribution_test;
DROP TABLE IF EXISTS wf_param_stability_test;
DROP TABLE IF EXISTS wf_necessity_test;
DROP TABLE IF EXISTS wf_mechanism_fidelity_audit;
DROP TABLE IF EXISTS wf_condition_audit;
DROP TABLE IF EXISTS wf_mechanism_fingerprint;
DROP TABLE IF EXISTS wf_mechanism_statement;
DROP TABLE IF EXISTS wf_task_meta;
DELETE FROM wf_migration_ledger WHERE migration_version='001';
DROP TABLE IF EXISTS wf_migration_ledger;
