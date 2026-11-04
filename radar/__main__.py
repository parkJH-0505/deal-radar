"""CLI: python -m radar {probe|daily} [--date YYYY-MM-DD]"""
from __future__ import annotations

import argparse
import datetime as dt


def main() -> None:
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("cmd", choices=["probe", "daily"])
    ap.add_argument("--date", help="run as if today were this KST date (YYYY-MM-DD)")
    a = ap.parse_args()
    if a.cmd == "probe":
        from .probe import main as probe
        probe()
    else:
        from .pipeline import daily
        daily(dt.date.fromisoformat(a.date) if a.date else None)


if __name__ == "__main__":
    main()
