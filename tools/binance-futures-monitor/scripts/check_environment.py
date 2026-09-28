"""Check that the monitor venv loads this checkout and its runtime dependencies."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


def main() -> int:
    if sys.version_info < (3, 13):
        print("Binance Futures Monitor requires Python 3.13 or newer.", file=sys.stderr)
        return 1

    expected = (Path(__file__).resolve().parents[1] / "src" / "binance_futures_monitor" / "__init__.py").resolve()
    spec = importlib.util.find_spec("binance_futures_monitor")
    actual = Path(spec.origin).resolve() if spec and spec.origin else None
    if actual != expected:
        print(f"Monitor package is not installed from this checkout: {actual}", file=sys.stderr)
        return 1

    for module in ("mysql.connector", "dotenv", "requests", "solders", "web3"):
        try:
            importlib.import_module(module)
        except ImportError as exc:
            print(f"Monitor dependency unavailable: {module}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
