"""Probe round 2: where can 12-month head-count history come from?

A) NPS API: does getBassInfoSearchV2 / getDetailInfoSearchV2 /
   getPdAcctoSttusInfoSearchV2 accept a past dataCrtYm?
B) data.go.kr monthly CSV (15083277): how does the download button resolve?
"""
from __future__ import annotations

import json
import os
import re

from .http import find_items, find_key, get, mask_url, parse_body, shape
from .sources.nps import BASE

G = os.environ.get("DATA_GO_KR_KEY", "").strip()


def q(op, **p):
    params = {"serviceKey": G, "pageNo": 1, "numOfRows": 5, "dataType": "json", **p}
    try:
        b = parse_body(get(f"{BASE}/{op}", params))
    except Exception as e:  # noqa: BLE001
        return f"ERROR {mask_url(str(e))[:200]}", []
    return f"code={find_key(b, 'resultCode')} total={find_key(b, 'totalCount')}", find_items(b) or []


def part_a():
    print("===== A. NPS past months =====")
    name = "이수이앤씨"
    for ym in (None, "202608", "202601", "202509"):
        extra = {"dataCrtYm": ym} if ym else {}
        st, items = q("getBassInfoSearchV2", wkplNm=name, **extra)
        yms = sorted({i.get("dataCrtYm") for i in items})
        print(f"search ym={ym}: {st} items={len(items)} dataCrtYm={yms} seqs={[i.get('seq') for i in items][:5]}")
    st, items = q("getBassInfoSearchV2", wkplNm=name)
    seq = items[0].get("seq") if items else None
    print("seq", seq)
    if seq:
        for ym in (None, "202609", "202608", "202601"):
            extra = {"dataCrtYm": ym} if ym else {}
            st, it = q("getDetailInfoSearchV2", seq=seq, **extra)
            print(f"detail ym={ym}: {st} -> {[(i.get('jnngpCnt'), i.get('crrmmNtcAmt')) for i in it]}")
            st, it = q("getPdAcctoSttusInfoSearchV2", seq=seq, **extra)
            print(f"period ym={ym}: {st} -> {it}")
        for k in ("data_crt_ym", "crtYm", "ym", "dataCrtYmd"):
            st, it = q("getPdAcctoSttusInfoSearchV2", seq=seq, **{k: "202608"})
            print(f"period {k}=202608: {st} -> {it}")
    # a big, stable company to see how many workplaces / snapshot months
    st, items = q("getBassInfoSearchV2", wkplNm="오뚜기", numOfRows=20)
    print("오뚜기:", st, json.dumps([{k: i.get(k) for k in ("wkplNm", "seq", "dataCrtYm", "bzowrRgstNo")}
                                      for i in items[:8]], ensure_ascii=False))


def part_b():
    print("\n===== B. data.go.kr monthly CSV download wiring =====")
    page = "https://www.data.go.kr/data/15083277/fileData.do"
    html = get(page).text
    scripts = sorted(set(re.findall(r'<script[^>]+src="([^"]+)"', html)))
    print("scripts:", scripts[:40])
    # inline definition?
    for m in re.finditer(r"fileDataDown", html):
        s = html[max(0, m.start() - 300): m.start() + 900]
        if "function" in s:
            print("INLINE DEF:\n", s.replace("\n", " ")[:1200])
            break
    for src in scripts:
        url = src if src.startswith("http") else "https://www.data.go.kr" + src
        try:
            js = get(url, timeout=30).text
        except Exception:  # noqa: BLE001
            continue
        i = js.find("fileDataDown")
        if i >= 0:
            print(f"\nJS {src} contains fileDataDown:")
            print(js[max(0, i - 200): i + 1800].replace("\n", " "))
    # history / past versions hints
    for p in (r"selectFileDataHistory[^\"'<> ]{0,120}", r"이전[^<]{0,40}데이터", r"historyList[^\"'<> ]{0,80}",
              r"fileDetailSn[^\"'<>]{0,60}", r"uddi:[0-9a-f\-]{36}"):
        print(p, "->", sorted(set(re.findall(p, html)))[:10])
    # try the most common endpoints (status / type / size only)
    tries = [
        "https://www.data.go.kr/cmm/cmm/fileDownload.do?atchFileId=&fileDetailSn=1",
        "https://www.data.go.kr/tcs/dss/selectFileDataDownload.do?publicDataPk=15083277&fileDetailSn=1",
        "https://www.data.go.kr/tcs/dss/selectFileDataDownload.do?publicDataPk=15083277&fileDetailSn=1&dataNm=",
    ]
    for t in tries:
        try:
            import requests
            r = requests.get(t, timeout=60, stream=True, allow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0", "Referer": page})
            head = next(r.iter_content(300), b"")
            print("\nTRY", t, "->", r.status_code, r.headers.get("content-type"),
                  r.headers.get("content-length"), r.headers.get("content-disposition"),
                  "final:", r.url)
            print("   first bytes:", head[:200])
            r.close()
        except Exception as e:  # noqa: BLE001
            print("TRY", t, "error", e)


if __name__ == "__main__":
    print("key present:", bool(G))
    part_a()
    part_b()
