"""Zero-dependency test runner (pytest also works: `pytest tests/`)."""

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests.test_calibration as tc
import tests.test_engine_parity as tp
import tests.test_gate as tg
import tests.test_golden as tgold
import tests.test_run_backtest as tr
import tests.test_stats as ts

failed = 0
for mod in (ts, tg, tr, tgold, tc, tp):
    for name in dir(mod):
        if name.startswith("test_"):
            try:
                getattr(mod, name)()
                print(f"PASS {mod.__name__}.{name}")
            except Exception:
                failed += 1
                print(f"FAIL {mod.__name__}.{name}")
                traceback.print_exc()
sys.exit(1 if failed else 0)
