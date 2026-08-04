#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sync formal_submits display with live mounts; reject obsolete frost3w2g SOL 1h rows."""
from __future__ import print_function

import json
import os
import sys

ROOT = os.environ.get("VECTOR_ROOT", "/root")
sys.path.insert(0, ROOT)

import auto_trade_dual_engine_factory as dual


def main():
    result = dual.reconcile_formal_display()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
