# -*- coding: utf-8 -*-
"""Compat entry: single invent hub (legacy name hub_a_strict)."""
from __future__ import print_function

import os
import sys

os.environ["KDH_THIN_NS"] = ""
_ROOT = os.environ.get("VECTOR_ROOT") or "/root"
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import kimi_thin_recipe_loop_hub as hub  # noqa: E402

if __name__ == "__main__":
    sys.exit(hub.main() or 0)
