"""Probe round 3: automate the 국민연금 monthly CSV (data.go.kr fileData 15083277)
and look for past monthly versions (publicDataHistSn)."""
from __future__ import annotations

import io
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
    print("uddi:", uddi)

    js = S.get("https://www.data.go.kr/js/biz/datset/script_fileDetail.js", timeout=60).text
    print("endpoints in script_fileDetail.js:", sorted(set(re.findall(r"/tcs/[A-Za-z/]+\.do|/cmm/[A-Za-z/\-]+\.(?:do|json)", js))))
    for m in re.finditer(r"[Hh]ist", js):
        print("  hist ctx:", js[max(0, m.start() - 120): m.start() + 160].replace("\n", " ")[:280])
        break
    print("hist hints in page:", sorted(set(re.findall(r"[A-Za-z_]*[Hh]ist[A-Za-z_]*", html)))[:30])
    for m in re.finditer(r"publicDataHistSn|histSn|HistSn", html):
        print("  page ctx:", html[max(0, m.start() - 200): m.start() + 200].replace("\n", " ")[:400])

    r = S.post("https://www.data.go.kr/tcs/dss/selectFileDataDownload.do",
               data={"publicDataPk": PK, "publicDataDetailPk": uddi, "fileDetailSn": "1",
                     "publicDataTyCode": "PR0051"}, timeout=60)
    print("\nselectFileDataDownload:", r.status_code)
    j = json.loads(r.text)
    info = j.get("dataSetFileDetailInfo") or {}
    print("top keys:", list(j.keys()))
    print("status:", j.get("status"), "atchFileId:", j.get("atchFileId"), "fileDetailSn:", j.get("fileDetailSn"))
    print("info (non-empty):", {k: v for k, v in info.items() if v not in (None, "", [], 0)})

    atch, sn = j.get("atchFileId"), j.get("fileDetailSn")
    if not atch:
        return
    lim = S.post("https://www.data.go.kr/cmm/cmm/check-limit.json",
                 data={"atchFileId": atch, "fileDetailSn": sn}, timeout=60)
    print("\ncheck-limit:", lim.status_code, lim.text[:200])

    url = "https://www.data.go.kr/cmm/cmm/fileDownload.do"
    dr = S.get(url, params={"atchFileId": atch, "fileDetailSn": sn,
                            "dataNm": info.get("dataNm", "nps")}, stream=True, timeout=300)
    print("download:", dr.status_code, dr.headers.get("content-type"), dr.headers.get("content-length"),
          dr.headers.get("content-disposition"))
    total, buf = 0, io.BytesIO()
    for chunk in dr.iter_content(1 << 20):
        if total < 4096:
            buf.write(chunk[:4096])
        total += len(chunk)
    print("downloaded bytes:", total)
    head = buf.getvalue()
    for enc in ("utf-8-sig", "cp949"):
        try:
            txt = head.decode(enc)
            print(f"decoded as {enc}:")
            for line in txt.splitlines()[:4]:
                print("   ", line[:300])
            break
        except UnicodeDecodeError:
            continue


if __name__ == "__main__":
    main()
