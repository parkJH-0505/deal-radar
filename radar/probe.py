"""Probe every endpoint with a tiny request and print the response SHAPE.

Run from the 'probe' GitHub Action. Keys are masked; values are truncated.
The output tells us the real parameter/field names so the collectors can be
corrected without guessing.
"""
from __future__ import annotations

import json
import os
import re

from .http import HttpError, find_items, find_key, get, mask_url, parse_body, shape
from .sources import localdata as L
from .sources.nps import BASE as NPS_BASE, STYLES


def _show(title: str, fn) -> None:
    print(f"\n===== {title} =====", flush=True)
    try:
        r = fn()
    except Exception as e:  # noqa: BLE001 - probe must never crash
        print(f"ERROR {type(e).__name__}: {mask_url(str(e))[:600]}")
        return
    if r is None:
        return
    print(f"HTTP {r.status_code}  content-type={r.headers.get('content-type')}")
    try:
        body = parse_body(r)
    except Exception as e:  # noqa: BLE001
        print(f"unparsed body: {mask_url(r.text[:800])}  ({e})")
        return
    print("resultCode:", find_key(body, "resultCode", "status"),
          "| msg:", find_key(body, "resultMsg", "message"),
          "| total:", find_key(body, "totalCount", "total_count"))
    items = find_items(body) or []
    print(f"items: {len(items)}")
    if items:
        print("first item fields:")
        print(json.dumps(shape(items[0]), ensure_ascii=False, indent=1))
    else:
        print("envelope:", json.dumps(shape(body), ensure_ascii=False)[:1500])


def main() -> None:
    g = os.environ.get("DATA_GO_KR_KEY", "").strip()
    d = os.environ.get("DART_KEY", "").strip()
    print("keys present:", {"DATA_GO_KR_KEY": bool(g), "DART_KEY": bool(d)})

    if g:
        url = L.BASE.format(slug="food_manufacturing_processors", op="info")
        _show("localdata 식품제조가공업 /info (3 rows)",
              lambda: get(url, {"serviceKey": g, "pageNo": 1, "numOfRows": 3,
                                "returnType": "json"}))
        _show("localdata /info with cond[LCPMT_YMD::GTE]=20260901",
              lambda: get(url, {"serviceKey": g, "pageNo": 1, "numOfRows": 2,
                                "returnType": "json", "cond[LCPMT_YMD::GTE]": "20260901"}))
        hurl = L.BASE.format(slug="food_manufacturing_processors", op="history")
        for pname in ("cond[BASE_DATE::EQ]", "baseDate", "dataStdrDe", "cond[DATA_STDR_DE::EQ]"):
            _show(f"localdata /history param {pname}=20260101",
                  lambda p=pname: get(hurl, {"serviceKey": g, "pageNo": 1, "numOfRows": 2,
                                              "returnType": "json", p: "20260101"}))

        for style, names in STYLES.items():
            _show(f"NPS getBassInfoSearchV2 style={style} name=삼성전자",
                  lambda n=names: get(f"{NPS_BASE}/getBassInfoSearchV2",
                                      {"serviceKey": g, "pageNo": 1, "numOfRows": 3,
                                       "dataType": "json", n["name"]: "삼성전자"}))
        _show("NPS getBassInfoSearchV2 no filter",
              lambda: get(f"{NPS_BASE}/getBassInfoSearchV2",
                          {"serviceKey": g, "pageNo": 1, "numOfRows": 2, "dataType": "json"}))
        # detail / period need a seq: take it from the search above
        try:
            body = parse_body(get(f"{NPS_BASE}/getBassInfoSearchV2",
                                  {"serviceKey": g, "pageNo": 1, "numOfRows": 1,
                                   "dataType": "json", "wkplNm": "삼성전자"}))
            it = (find_items(body) or [{}])[0]
            seq = find_key(it, "seq")
            ym = find_key(it, "dataCrtYm")
            print("\nseq for detail/period:", seq, "dataCrtYm:", ym)
            if seq:
                _show("NPS getDetailInfoSearchV2",
                      lambda: get(f"{NPS_BASE}/getDetailInfoSearchV2",
                                  {"serviceKey": g, "seq": seq, "dataType": "json"}))
                _show("NPS getPdAcctoSttusInfoSearchV2 (no ym)",
                      lambda: get(f"{NPS_BASE}/getPdAcctoSttusInfoSearchV2",
                                  {"serviceKey": g, "seq": seq, "dataType": "json"}))
                _show("NPS getPdAcctoSttusInfoSearchV2 ym=202606",
                      lambda: get(f"{NPS_BASE}/getPdAcctoSttusInfoSearchV2",
                                  {"serviceKey": g, "seq": seq, "dataCrtYm": "202606",
                                   "dataType": "json"}))
        except Exception as e:  # noqa: BLE001
            print("seq lookup failed:", mask_url(str(e))[:300])

        # monthly full CSV (no key): find the download wiring on the file page
        print("\n===== NPS monthly file page (download wiring) =====")
        try:
            html = get("https://www.data.go.kr/data/15083277/fileData.do").text
            pats = [r"fileDownload[^\"'<>]{0,200}", r"atchFileId[^\"'<>]{0,120}",
                    r"uddi:[0-9a-f\-]{20,}", r"fn_fileData[A-Za-z]*\([^)]{0,200}\)",
                    r"publicDataDetailPk[^\"'<>]{0,80}", r"/tcs/dss/[A-Za-z/\.]+"]
            for p in pats:
                found = sorted(set(re.findall(p, html)))[:6]
                print(p, "->", found)
        except HttpError as e:
            print("file page failed:", e)

    if d:
        _show("DART list.json F/E 2026-03-31",
              lambda: get("https://opendart.fss.or.kr/api/list.json",
                          {"crtfc_key": d, "bgn_de": "20260331", "end_de": "20260331",
                           "pblntf_ty": "F", "corp_cls": "E", "page_count": 3}))
        try:
            j = get("https://opendart.fss.or.kr/api/list.json",
                    {"crtfc_key": d, "bgn_de": "20260331", "end_de": "20260331",
                     "pblntf_ty": "F", "corp_cls": "E", "page_count": 1}).json()
            print("list total_count:", j.get("total_count"), "total_page:", j.get("total_page"))
            code = (j.get("list") or [{}])[0].get("corp_code")
            if code:
                _show("DART company.json", lambda: get(
                    "https://opendart.fss.or.kr/api/company.json",
                    {"crtfc_key": d, "corp_code": code}))
        except Exception as e:  # noqa: BLE001
            print("DART follow-up failed:", mask_url(str(e))[:300])


if __name__ == "__main__":
    main()
