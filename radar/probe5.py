"""Probe round 5: how does a row of the history list open a past monthly file?"""
from __future__ import annotations

import re

import requests

PK = "15083277"
PAGE = f"https://www.data.go.kr/data/{PK}/fileData.do"
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (deal-radar research)", "Referer": PAGE})


def main():
    html = S.get(PAGE, timeout=60).text
    uddi = re.search(r"uddi:[0-9a-f\-]{36}", html).group(0)
    h = S.post("https://www.data.go.kr/tcs/dss/selectHistAndCsvData.do",
               data={"publicDataPk": PK, "publicDataDetailPk": uddi}, timeout=60).text
    i = h.find("2025-10-31")
    print("row ctx around 2025-10-31:\n", re.sub(r"\s+", " ", h[max(0, i - 1500): i + 600]))
    calls = re.findall(r"(?:onclick|href)=\"([^\"]{0,300})\"", h)
    print("\ndistinct onclick/href (first 15):")
    for c in list(dict.fromkeys(calls))[:15]:
        print("  ", c[:300])
    print("\nuddi in hist:", list(dict.fromkeys(re.findall(r"uddi:[0-9a-f\-]{36}", h)))[:10])
    print("numbers near fn_:", re.findall(r"fn_[A-Za-z]+\([^)]{0,200}\)", h)[:10])


if __name__ == "__main__":
    main()
