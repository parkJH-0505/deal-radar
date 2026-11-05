"""End-to-end run of the daily pipeline against fake APIs (no network)."""
import datetime as dt
import io
import json
import zipfile
from urllib.parse import parse_qs

import pytest

from radar import pipeline as P
from radar.sources import dart as D
from radar.sources import localdata as L
from radar.sources import nps as N
from radar.sources import nps_monthly as NM
from tests.test_nps_monthly import csv_bytes, row


class FakeResp:
    def __init__(self, obj=None, content=None, status=200):
        self.status_code = status
        self.headers = {"content-type": "application/json"}
        if content is not None:
            self.content = content
            self.text = ""
        else:
            self.text = json.dumps(obj, ensure_ascii=False)
            self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


AUDIT_XML = """<DOCUMENT><P>포괄손익계산서</P><P>(단위 : 원)</P>
<TABLE><TR><TD>매출액</TD><TD>22</TD><TD>31,250,000,000</TD><TD>18,400,000,000</TD></TR>
<TR><TD>영업이익</TD><TD></TD><TD>3,600,000,000</TD><TD>1,100,000,000</TD></TR></TABLE></DOCUMENT>"""


def _zip(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("doc.xml", text.encode("utf-8"))
    return b.getvalue()


def fake_get(url, params=None, **kw):
    p = params or {}
    if "opendart" in url:
        if url.endswith("list.json"):
            bgn = p["bgn_de"]
            rows = []
            if bgn.startswith("202403"):  # an old auditee seen in 2024
                rows.append({"corp_code": "00000001", "corp_name": "오래된회사",
                             "rcept_dt": "20240329", "rcept_no": "20240329000001",
                             "report_nm": "감사보고서 (2023.12)"})
            if bgn.startswith("202603"):
                rows += [
                    {"corp_code": "00000001", "corp_name": "오래된회사", "rcept_dt": "20260330",
                     "rcept_no": "20260330000001", "report_nm": "감사보고서 (2025.12)"},
                    {"corp_code": "00000002", "corp_name": "(주)해담식품", "rcept_dt": "20260331",
                     "rcept_no": "20260331000002", "report_nm": "감사보고서 (2025.12)"},
                    {"corp_code": "00000003", "corp_name": "제3호유동화전문유한회사",
                     "rcept_dt": "20260331", "rcept_no": "20260331000003",
                     "report_nm": "감사보고서 (2025.12)"},
                ]
            if not rows:
                return FakeResp({"status": "013", "message": "no data"})
            return FakeResp({"status": "000", "total_page": 1, "list": rows})
        if url.endswith("company.json"):
            code = p["corp_code"]
            est = {"00000002": "20220610", "00000003": "20230101"}.get(code, "19990101")
            nm = {"00000002": "(주)해담식품", "00000003": "제3호유동화전문유한회사"}.get(code, "오래된회사")
            return FakeResp({"status": "000", "corp_code": code, "corp_name": nm,
                             "est_dt": est, "induty_code": "10799",
                             "bizr_no": "1234567890", "adres": "경기도 화성시"})
        if url.endswith("document.xml"):
            return FakeResp(content=_zip(AUDIT_XML))
    if "1741000" in url:
        rows = [
            {"BPLC_NM": "주식회사 해담식품", "LCPMT_YMD": "20220302", "SALS_STTS_NM": "영업",
             "ROAD_NM_ADDR": "경기도 화성시 a"},
            {"BPLC_NM": "해담식품 제2공장", "LCPMT_YMD": "20260810", "SALS_STTS_NM": "영업",
             "ROAD_NM_ADDR": "충청북도 음성군 b"},
            {"BPLC_NM": "새싹푸드", "LCPMT_YMD": "20250105", "SALS_STTS_NM": "영업"},
            {"BPLC_NM": "새싹푸드 2공장", "LCPMT_YMD": "20260901", "SALS_STTS_NM": "영업"},
        ]
        page = int(p.get("pageNo", 1))
        items = rows if page == 1 else []
        return FakeResp({"response": {"header": {"resultCode": "00"},
                                      "body": {"items": {"item": items},
                                               "totalCount": len(rows)}}})
    if "B552015" in url:
        op = url.rsplit("/", 1)[-1]
        if op == "getBassInfoSearchV2":
            nm = p.get("wkplNm", "")
            items = []
            if "삼성전자" in nm:
                items = [{"wkplNm": "삼성전자(주)", "seq": "1", "bzowrRgstNo": "124810****"}]
            if "해담" in nm:
                items = [{"wkplNm": "(주)해담식품", "seq": "77", "bzowrRgstNo": "123456****",
                          "dataCrtYm": "202610"}]
            return FakeResp({"response": {"header": {"resultCode": "00"},
                                          "body": {"items": {"item": items}}}})
        if op == "getDetailInfoSearchV2":
            return FakeResp({"response": {"header": {"resultCode": "00"}, "body": {"items": {
                "item": {"jnngpCnt": "90", "crrmmNtcAmt": "36000000", "vldtVlKrnNm": "식품"}}}}})
        if op == "getPdAcctoSttusInfoSearchV2":
            return FakeResp({"response": {"header": {"resultCode": "00"}, "body": {"items": {
                "item": {"nwAcqzrCnt": "6", "lssJnngpCnt": "2"}}}}})
    raise AssertionError(f"unexpected url {url}")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    for mod in (D, L, N):
        monkeypatch.setattr(mod, "get", fake_get)
    monkeypatch.setattr(P, "DATA", tmp_path)
    monkeypatch.setattr(P, "STATE", tmp_path / "state")
    monkeypatch.setenv("DATA_GO_KR_KEY", "k")
    monkeypatch.setenv("DART_KEY", "k")
    # 국민연금 monthly panel: 3 synthetic months, no network
    monkeypatch.setattr(NM, "update_archive", lambda *a, **k: [])
    months = tmp_path / "nps" / "months"
    for ym, hc in (("202509", 40), ("202603", 60), ("202609", 90)):
        rows = [row(f"{ym[:4]}-{ym[4:]}", "(주)해담식품", "123456", hc, hc * 400_000),
                # NPS-only discovery: a young corp doubling head-count
                row(f"{ym[:4]}-{ym[4:]}", "(주)조용한성장", "998877",
                    {"202509": 30, "202603": 45, "202609": 70}[ym], 1, adopt="2023-02-01",
                    icode="912290")]
        _, agg = NM.aggregate(csv_bytes(rows), adopt_after="20200101", min_headcount=5, exclude=[])
        NM.save_month(months / f"{ym}.tsv.gz", agg)
    return tmp_path


def test_daily_end_to_end(sandbox):
    out = P.daily(dt.date(2026, 11, 5))
    names = {c["name"]: c for c in out["queue"]}
    assert "(주)해담식품" in names, out
    h = names["(주)해담식품"]
    assert h["sources"] == ["dart", "localdata", "nps"]    # all three sources agree
    assert h["financials"]["revenue"] == 31_250_000_000
    assert h["nps"]["headcount"] == 90 and h["nps"]["growth_12m"] > 0.5
    assert h["change"] == "new"
    assert "오래된회사" not in names                         # not a first-time auditee
    assert not any("유동화" in n for n in names)            # SPC excluded
    assert "새싹푸드" in names                               # HQ name, not "새싹푸드 2공장"
    assert names["새싹푸드"]["sources"] == ["localdata"]
    q = names["(주)조용한성장"]                              # found by the NPS panel scan alone
    assert q["sources"] == ["nps"] and q["nps"]["growth_12m"] > 1
    assert len(out["queue"]) == 3, [c["name"] for c in out["queue"]]
    # second run: nothing new, everything cached
    out2 = P.daily(dt.date(2026, 11, 6))
    assert all(c["change"] != "new" for c in out2["queue"])
    assert (sandbox / "watchlist.json").exists()
    assert (sandbox / "runs" / "2026-11-05.md").exists()
