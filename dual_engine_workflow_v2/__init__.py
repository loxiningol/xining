# -*- coding: utf-8 -*-
"""Strategy creation workflow v2 + STEP A production/training refactor.

Isolated behind workflow_flags.creation_entry; legacy dual-engine remains available.
STEP A adds immutable mechanism_spec, 20-split tests, Gates 0–7, failure KB.
"""
from .config import (
    WORKFLOW_VERSION,
    MIGRATION_VERSION,
    load_flags,
    save_flags,
    creation_entry,
    set_creation_entry,
    CODE_VERSION,
)
from .pipeline import run_creation_pipeline_v2, start_creation_task_v2
from .pipeline_step_a import (
    run_creation_pipeline_step_a,
    start_creation_task_step_a,
)
from .store import migrate_forward, migrate_rollback, workflow_status_payload
from .step_a_config import STEP_A_CODE_VERSION

__all__ = [
    "WORKFLOW_VERSION",
    "MIGRATION_VERSION",
    "CODE_VERSION",
    "STEP_A_CODE_VERSION",
    "load_flags",
    "save_flags",
    "creation_entry",
    "set_creation_entry",
    "run_creation_pipeline_v2",
    "start_creation_task_v2",
    "run_creation_pipeline_step_a",
    "start_creation_task_step_a",
    "migrate_forward",
    "migrate_rollback",
    "workflow_status_payload",
]
