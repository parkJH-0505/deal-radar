import datetime as dt

from radar.sources import nps_monthly as NM

HEADER = ("자료생성년월,사업장명,사업자등록번호,사업장가입상태코드 1 등록 2 탈퇴,우편번호,사업장지번상세주소,"
          "사업장도로명상세주소,고객법정동주소코드,고객행정동주소코드,법정동주소광역시도코드,법정동주소광역시시군구코드,"
          "법정동주소광역시시군구읍면동코드,사업장형태구분코드 1 법인 2 개인,사업장업종코드,사업장업종코드명,적용일자,"
          "재등록일자,탈퇴일자,가입자수,당월고지금액,신규취득자수,상실가입자수")


def row(ym, name, b6, hc, bill, adopt="2022-03-01", state="1", form="1", icode="107999"):
    return (f"{ym},{name},{b6},{state},12345,경기도 화성시,경기도 화성시 향남로,1,1,41,590,101,{form},"
            f"{icode},기타 식료품 제조업,{adopt},,,{hc},{bill},1,0")


def csv_bytes(rows):
    return ("\n".join([HEADER] + rows) + "\n").encode("cp949")


def test_aggregate_filters_and_sums():
    raw = csv_bytes([
        row("2026-09", "(주)해담식품", "123456", 60, 30_000_000),
        row("2026-09", "해담식품 제2공장", "123456", 30, 12_000_000),          # same company, 2nd site
        row("2026-09", "오래된회사(주)", "222222", 300, 1, adopt="1995-01-01"),  # too old
        row("2026-09", "개인상점", "333333", 50, 1, form="2"),                 # 개인
        row("2026-09", "(주)건설/일용/○○ 신축공사 중 철골공사", "444444", 80, 1),  # temp site
        row("2026-09", "탈퇴회사(주)", "555555", 40, 1, state="2"),
    ])
    ym, rows = NM.aggregate(raw, adopt_after="20200101", min_headcount=5, exclude=["공사$"])
    assert ym == "202609"
    assert len(rows) == 1
    r = rows[0]
    assert r["key"] == "123456|해담식품" and r["headcount"] == 90 and r["sites"] == 2


def test_panel_metrics_and_scan(tmp_path):
    d = tmp_path / "months"
    def month(ym, hc, bill):
        _, rows = NM.aggregate(csv_bytes([row(f"{ym[:4]}-{ym[4:]}", "(주)해담식품", "123456", hc, bill),
                                          row(f"{ym[:4]}-{ym[4:]}", "느린회사(주)", "777777", 40, 1)]),
                               adopt_after="20200101", min_headcount=5, exclude=[])
        NM.save_month(d / f"{ym}.tsv.gz", rows)
    month("202509", 40, 40 * 300_000)
    month("202603", 60, 60 * 320_000)
    month("202609", 90, 90 * 360_000)
    yms, data = NM.build_panel(d)
    m = NM.metrics_for("123456|해담식품", yms, data)
    assert m["growth_12m"] == 1.25 and m["growth_6m"] == 0.5
    assert m["bill_per_head"] == 360_000 and m["bill_per_head_change_12m"] == 0.2
    found = NM.scan(yms, data, today=dt.date(2026, 11, 5), max_age_years=6, min_headcount=30,
                    min_growth_12m=0.5, min_growth_6m=0.3)
    assert [f["name"] for f in found] == ["(주)해담식품"]


def test_aggregate_keeps_sole_proprietors_separately_any_age():
    raw = csv_bytes([
        row("2026-09", "(주)해담식품", "123456", 60, 30_000_000),
        row("2026-09", "개인상점", "333333", 50, 1, form="2", adopt="2009-05-01"),   # old 개인: kept
        row("2026-09", "아주작은가게", "444444", 2, 1, form="2"),                     # < 3명: dropped
    ])
    indiv = []
    ym, rows = NM.aggregate(raw, adopt_after="20200101", min_headcount=5, exclude=[], indiv_out=indiv)
    assert [r["name"] for r in rows] == ["(주)해담식품"]
    assert [r["name"] for r in indiv] == ["개인상점"] and indiv[0]["adopt"] == "20090501"
