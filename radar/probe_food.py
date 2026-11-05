"""Probe 식품안전나라 open API sample endpoints (no key needed for /sample/)."""
from __future__ import annotations

import json
import os

import requests

BASE = "http://openapi.foodsafetykorea.go.kr/api"
KEY = os.environ.get("FOODSAFETY_KEY") or "sample"
SERVICES = os.environ.get("PROBE_ARGS", "I1250 C002 I2829 I0580 I0490").split()


def show(url: str) -> None:
    print("\n=====", url.replace(KEY, "***") if KEY != "sample" else url)
    try:
        r = requests.get(url, timeout=60)
        print("HTTP", r.status_code, r.headers.get("content-type"))
        try:
            j = r.json()
            print(json.dumps(j, ensure_ascii=False)[:2500])
        except ValueError:
            print(r.text[:1500])
    except Exception as e:  # noqa: BLE001
        print("ERROR", type(e).__name__, str(e)[:300])


def main() -> None:
    for svc in SERVICES:
        show(f"{BASE}/{KEY}/{svc}/json/1/3")
    # filter syntax checks on the product-report service
    show(f"{BASE}/{KEY}/I1250/json/1/3/PRMS_DT=20260901")
    show(f"{BASE}/{KEY}/I1250/json/1/3/BSSH_NM=오뚜기")


if __name__ == "__main__":
    main()
