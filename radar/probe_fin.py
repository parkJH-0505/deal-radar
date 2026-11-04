"""Dump real 감사보고서 text around the income statement to fix the parser."""
from __future__ import annotations

import io
import os
import re
import sys
import zipfile

from .http import get
from .sources import dart as D

RCEPTS = sys.argv[1:] or os.environ.get("PROBE_ARGS", "").split()


def main() -> None:
    key = os.environ["DART_KEY"]
    for no in RCEPTS:
        print(f"\n######## {no}")
        r = get(f"{D.BASE}/document.xml", {"crtfc_key": key, "rcept_no": no}, timeout=120)
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        print("files:", [(n, zf.getinfo(n).file_size) for n in zf.namelist()])
        raw = zf.read(zf.namelist()[0]).decode("utf-8", "replace")
        i = raw.find("매출")
        print("--- RAW around first 매출 (", i, ")")
        print(raw[max(0, i - 1500):i + 1500])
        text = D.audit_text(key, no)
        hits = [m.start() for m in re.finditer(r"\s*".join("손익계산서"), text)]
        print("--- TEXT len", len(text), "손익계산서 at", hits[:10])
        for m in re.finditer(r"매\s*출\s*액|영\s*업\s*수\s*익", text):
            print("--- TEXT around 매출 @", m.start())
            print(text[max(0, m.start() - 300):m.start() + 700])
            break
        print("--- extract:", {k: v for k, v in D.extract_financials(text).items()})


if __name__ == "__main__":
    main()
