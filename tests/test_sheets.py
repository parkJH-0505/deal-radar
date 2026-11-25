from radar import sheets as S


def test_upsert_preserves_user_columns_and_adds_new_rows():
    legacy = [c for c in S.CAND_COLS if c not in ("등급", S.REG)]  # sheet created before these columns
    existing = [legacy + ["내 메모(사용자 추가)"],
                ["dart:1", "딥다이브", "관심", "+수익구조", "광고비 비중 확인", "80", "DART", "5.1",
                 "", "", "915", "11", "없음 확인", "", "", "", "", "", "2026-11-05", "2026-11-05", "따로 적은 것"]]
    grid = S.merge_candidates(existing, [
        {S.KEY: "dart:1", "회사명": "딥다이브", "등급": "B", "점수": 84, "상태": "신규", "내 코멘트": "",
         "첫 포착": "2026-11-09", "최근 포착": "2026-11-09"},
        {S.KEY: "nps:123456:지신", "회사명": "지신", "점수": 82, "최근 포착": "2026-11-09"},
    ])
    h = grid[0]
    row = dict(zip(h, grid[1]))
    assert row["상태"] == "관심" and row["내 코멘트"] == "광고비 비중 확인"   # user cols untouched
    assert row["점수"] == "84" and row["첫 포착"] == "2026-11-05"           # system cols refreshed, first-seen kept
    assert row["내 메모(사용자 추가)"] == "따로 적은 것"                      # user's extra column survives
    assert h.index("등급") == h.index("회사명") + 1 and row["등급"] == "B"     # new column inserted in place
    new = dict(zip(h, grid[2]))
    assert new["상태"] == "신규" and new["첫 포착"] == "2026-11-09"


def test_snapshot_and_lens_markdown():
    rec = {S.KEY: "dart:1", "회사명": "딥다이브", "상태": "패스", "이유 태그": "-이미 큼"}
    cand = [S.CAND_COLS, [rec.get(c, "") for c in S.CAND_COLS]]
    lens = [S.LENS_COLS, ["2026-11-05", "직접", "B2B 반복매출이면 인원이 적어도 본다", "", ""],
            ["2026-11-05", "Claude", "얼리 스테이지", "사용자 메시지", "가중치"], ["", "", "", "", ""]]
    snap = S.snapshot(cand, lens)
    assert snap["candidates"][0]["상태"] == "패스" and len(snap["lens"]) == 2
    md = S.lens_markdown(snap)
    assert "B2B 반복매출" in md and md.index("최우선") < md.index("Claude가 피드백")


def test_only_ab_create_rows_but_existing_rows_get_regraded():
    existing = [S.CAND_COLS, [("dart:1" if c == S.KEY else "") for c in S.CAND_COLS]]
    ups = [{S.KEY: "dart:1", "등급": "C"}, {S.KEY: "nps:2:가", "등급": "A"},
           {S.KEY: "nps:3:나", "등급": "B"}, {S.KEY: "nps:4:다", "등급": "D"}, {S.KEY: "loc:옛형식"}]
    keys = [u[S.KEY] for u in S.only_listable(existing, ups)]
    assert keys == ["dart:1", "nps:2:가", "nps:3:나", "loc:옛형식"]


def test_align_rows_follows_sheet_header():
    header, rows = S.align_rows(["날짜", S.KEY, "판정"], [{"날짜": "d", S.KEY: "k", "등급": "C", "판정": "보류"}])
    assert header == ["날짜", S.KEY, "판정", "등급"] and rows == [["d", "k", "보류", "C"]]


def test_registration_date_first_column_new_rows_and_backfill():
    legacy = [c for c in S.CAND_COLS if c != S.REG]
    old = {S.KEY: "dart:1", "회사명": "딥다이브", "상태": "관심"}
    existing = [legacy, [old.get(c, "") for c in legacy]]
    grid = S.merge_candidates(existing, [{S.KEY: "nps:2:가", "등급": "A", "회사명": "가"},
                                         {S.KEY: "dart:1", S.REG: "2030-01-01", "점수": 90}],
                              registered={"dart:1": "2026-11-05"}, today="2026-11-25")
    h = grid[0]
    assert h[0] == S.REG
    rows = {r[h.index(S.KEY)]: dict(zip(h, r)) for r in grid[1:]}
    assert rows["dart:1"][S.REG] == "2026-11-05"          # backfilled from judgments, not overwritten
    assert rows["dart:1"]["상태"] == "관심" and rows["dart:1"]["점수"] == "90"
    assert rows["nps:2:가"][S.REG] == "2026-11-25"        # new row: today


def test_registration_dates_from_judgments(tmp_path):
    (tmp_path / "2026-11-05.md").write_text("## 생존\n| 키 | 회사 |\n|---|---|\n| dart:1 | a |\n", encoding="utf-8")
    (tmp_path / "2026-11-07.md").write_text(
        "## 판정\n| 키 | 등급 | 회사 |\n|---|---|---|\n| dart:1 | A | a |\n| nps:2:b | C | b |\n| nps:3:c | B | c |\n",
        encoding="utf-8")
    assert S.registration_dates(tmp_path) == {"dart:1": "2026-11-05", "nps:3:c": "2026-11-07"}
