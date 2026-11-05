"""Probe 식품안전나라 with the real per-service keys (masked in output)."""
from __future__ import annotations

import collections
import datetime as dt
import os

import requests

BASE = "http://openapi.foodsafetykorea.go.kr/api"
K1 = os.environ.get("FOODSAFETY_KEY", "")
K2 = os.environ.get("FOODSAFETY_KEY_HACCP", "")


def call(key, svc, a, b, flt=""):
    url = f"{BASE}/{key}/{svc}/json/{a}/{b}" + (f"/{flt}" if flt else "")
    r = requests.get(url, timeout=120)
    j = r.json()
    blk = j.get(svc) or {}
    return r.status_code, blk.get("RESULT"), int(blk.get("total_count") or 0), blk.get("row") or [], j


def main() -> None:
    print("keys set:", bool(K1), bool(K2))
    since = (dt.date.today() - dt.timedelta(days=180)).strftime("%Y%m%d")
    one = (dt.date.today() - dt.timedelta(days=30)).strftime("%Y%m%d")
    for svc, key, fld in (("I1250", K1, "PRMS_DT"), ("I0580", K2, "HACCP_APPN_DT")):
        print(f"\n===== {svc}")
        st, res, tot, rows, j = call(key, svc, 1, 5)
        print("no filter:", st, res, "total", tot, "fields", sorted(rows[0]) if rows else str(j)[:300])
        for f in (since, one):
            st, res, tot, rows, j = call(key, svc, 1, 1000, f"{fld}={f}")
            ds = collections.Counter((r.get(fld) or "")[:6] for r in rows)
            print(f"{fld}={f}:", st, res, "total", tot, "got", len(rows),
                  "min", min((r.get(fld) for r in rows), default=None),
                  "max", max((r.get(fld) for r in rows), default=None), "months", dict(sorted(ds.items())[-8:]))
        st, res, tot, rows, j = call(key, svc, 1, 1001)
        print("1001 rows request:", st, res, len(rows))


if __name__ == "__main__":
    main()
