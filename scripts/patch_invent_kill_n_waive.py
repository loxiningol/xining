# -*- coding: utf-8 -*-
"""Permanently kill invent trade_count waive path (Phase A task ②).

Patches scripts/kimi_dual_http_create_20260906.py in-place on VECTOR_ROOT.
Safe while hubs are stopped. Idempotent.
"""
from __future__ import print_function

import os
import re
import sys

MARKER = "SMALL_N_RIGOR_NEVER_WAIVE_V1"

REPLACEMENT = '''
def _waive_trade_count_only(ev):
    """%s: trade_count waive permanently disabled; full gate required."""
    from dual_engine_workflow_v2.creation_small_n_rigor import never_waive_trade_count
    return never_waive_trade_count(ev)
'''.strip() % MARKER


def patch_file(path):
    with open(path, "r") as f:
        text = f.read()
    if MARKER in text:
        return {"ok": True, "already": True, "path": path}
    # Replace function body from def _waive... through next top-level def
    pat = re.compile(
        r"\ndef _waive_trade_count_only\(ev\):.*?(?=\n\ndef |\n[A-Z_][A-Z0-9_]* =)",
        re.S,
    )
    if not pat.search(text):
        return {"ok": False, "error": "function_not_found", "path": path}
    new = pat.sub("\n\n" + REPLACEMENT + "\n\n", text, count=1)
    # neutralize false n_waived emit when mount succeeds via non-waive path
    new = new.replace('"n_waived": True,', '"n_waived": False,  # waive killed')
    new = new.replace(
        'Authority: isolated_cap hit_floor + E_raw>=0.003 (trade_count may be waived).',
        'Authority: isolated_cap hit_floor + E_raw>=0.003 + full quality gate (trade_count NOT waived).',
    )
    tmp = path + ".tmp_never_waive"
    with open(tmp, "w") as f:
        f.write(new)
    os.rename(tmp, path)
    return {"ok": True, "already": False, "path": path}


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    root = os.environ.get("VECTOR_ROOT") or "/root"
    path = argv[0] if argv else os.path.join(root, "scripts", "kimi_dual_http_create_20260906.py")
    out = patch_file(path)
    print(out)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
