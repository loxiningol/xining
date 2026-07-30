# -*- coding: utf-8 -*-
"""Re-export canonical 三复核 lexicon for the auto_driver package."""
from __future__ import print_function

import sys
from pathlib import Path

# Ensure repo root is importable (VECTOR_ROOT layout + local workspace).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dual_engine_workflow_v2.review_lexicon import *  # noqa: E402,F401,F403
