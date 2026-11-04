"""Probe round 4: past monthly versions of the 국민연금 CSV (selectHistAndCsvData.do)."""
from __future__ import annotations

import json
import re

import requests

PK = "15083277"
PAGE = f"https://www.data.go.kr/data/{PK}/fileData.do"
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (deal-radar research)", "Referer": PAGE})


def main():
    html = S.get(PAGE, timeout=60).text
    uddi = re.search(r"uddi:[0-9a-f\-]{36}", html).group(0)
    for m in re.finditer(r"fn_histAndCsvData|fileHistAndCsvData", html):
        print("page ctx:", html[max(0, m.start() - 300): m.start() + 500].replace("\n", " ")[:800])
    js = S.get("https://www.data.go.kr/js/biz/datset/script_fileDetail.js", timeout=60).text
    i = js.find("selectHistAndCsvData")
    print("\njs ctx:", js[max(0, i - 900): i + 900].replace("\n", " "))

    for data in ({"publicDataPk": PK, "publicDataDetailPk": uddi},
                 {"publicDataPk": PK}):
        r = S.post("https://www.data.go.kr/tcs/dss/selectHistAndCsvData.do", data=data, timeout=60)
        body = r.text
        print(f"\nPOST {data} -> {r.status_code} {r.headers.get('content-type')} len={len(body)}")
        ids = re.findall(r"FILE_\d{12,}", body)
        print("atchFileIds:", list(dict.fromkeys(ids))[:40])
        print("dates:", list(dict.fromkeys(re.findall(r"20\d\d[-.]\d\d[-.]\d\d", body)))[:40])
        print("histSn:", list(dict.fromkeys(re.findall(r"publicDataHistSn\D{0,5}(\d+)", body)))[:40])
        print("snippet:", body[:1500].replace("\n", " "))
        try:
            j = json.loads(body)
            print("json keys:", list(j)[:20])
        except ValueError:
            pass

    # try downloading the OLDEST few-hundred bytes of a past version if found
    r = S.post("https://www.data.go.kr/tcs/dss/selectHistAndCsvData.do",
               data={"publicDataPk": PK, "publicDataDetailPk": uddi}, timeout=60)
    ids = list(dict.fromkeys(re.findall(r"FILE_\d{12,}", r.text)))
    for atch in ids[1:3] + ids[-1:]:
        lim = S.post("https://www.data.go.kr/cmm/cmm/check-limit.json",
                     data={"atchFileId": atch, "fileDetailSn": "1"}, timeout=60)
        d = S.get("https://www.data.go.kr/cmm/cmm/fileDownload.do",
                  params={"atchFileId": atch, "fileDetailSn": "1", "dataNm": "nps"},
                  stream=True, timeout=120)
        first = next(d.iter_content(600), b"")
        d.close()
        try:
            line2 = first.decode("cp949", "ignore").splitlines()[1][:120]
        except IndexError:
            line2 = first[:120]
        print(f"\n{atch}: limit={lim.text[:60]} dl={d.status_code} len={d.headers.get('content-length')} row1={line2}")


if __name__ == "__main__":
    main()
