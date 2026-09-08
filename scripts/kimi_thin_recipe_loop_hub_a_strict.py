# -*- coding: utf-8 -*-
"""Hub-a thin invent: unified blunt + FREE_CREATE (was calibrate Phase E).

Delegates to kimi_thin_recipe_loop_hub_unified.py with empty NS.
"""
from __future__ import print_function

import os
import sys

os.environ.setdefault("KDH_THIN_NS", "")
os.environ.setdefault("KDH_FREE_CREATE", "1")
os.environ.setdefault("KDH_INVENT_STRICT_BIND", "1")

_ROOT = os.environ.get("VECTOR_ROOT") or "/root"
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import kimi_thin_recipe_loop_hub_unified as hub  # noqa: E402

if __name__ == "__main__":
    sys.exit(hub.main() or 0)
