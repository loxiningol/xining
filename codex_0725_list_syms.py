# -*- coding: utf-8 -*-
import re, inspect, backtest_engine_v2 as bt
path = inspect.getsourcefile(bt)
text = open(path).read()
keys = sorted(set(re.findall(r'"([A-Z0-9]+-USDT-SWAP)"', text)))
print('count', len(keys))
for k in keys:
    print(k)
print('---snap---')
lines = open('/tmp/codex0725_snap4.log').read().splitlines()
for line in lines[-25:]:
    print(line)
