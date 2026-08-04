# -*- coding: utf-8 -*-
import ast
import json
from pathlib import Path
import tempfile
import unittest


def _load_roster_helpers(auto_root):
    source = Path(__file__).with_name("web_server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"_vector_load_json", "_vector_configured_symbols"}
    ]
    namespace = {
        "_vector_Path": Path,
        "_vector_json": json,
        "_VECTOR_AUTO": Path(auto_root),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "web_server.py", "exec"), namespace)
    return namespace["_vector_configured_symbols"]


class WebAutoTradeRosterTest(unittest.TestCase):
    def test_discovers_new_five_minute_daemon_configs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for asset in ("sol", "eth", "xrp"):
                (root / ("formal_daemon_config_%s_5m.json" % asset)).write_text(
                    json.dumps({
                        "symbol": "%s-USDT-SWAP" % asset.upper(),
                        "timeframe": "5m",
                        "strategy_keys": ["%s5_live" % asset],
                    }),
                    encoding="utf-8",
                )
            (root / "formal_daemon_config_bad_5m.json").write_text(
                json.dumps({"symbol": "BAD-USDT-SWAP", "timeframe": "15m"}),
                encoding="utf-8",
            )

            discover = _load_roster_helpers(root)
            symbols = discover("5m", ("BTC-USDT-SWAP",))

        self.assertEqual("BTC-USDT-SWAP", symbols[0])
        self.assertIn("SOL-USDT-SWAP", symbols)
        self.assertIn("ETH-USDT-SWAP", symbols)
        self.assertIn("XRP-USDT-SWAP", symbols)
        self.assertNotIn("BAD-USDT-SWAP", symbols)
        self.assertEqual(len(symbols), len(set(symbols)))


if __name__ == "__main__":
    unittest.main()
