from radar import sheets as S


def test_upsert_preserves_user_columns_and_adds_new_rows():
    existing = [S.CAND_COLS + ["내 메모(사용자 추가)"],
                ["dart:1", "딥다이브", "관심", "+수익구조", "광고비 비중 확인", "80", "DART", "5.1",
                 "", "", "915", "11", "없음 확인", "", "", "", "", "", "2026-11-05", "2026-11-05", "따로 적은 것"]]
    grid = S.merge_candidates(existing, [
        {S.KEY: "dart:1", "회사명": "딥다이브", "점수": 84, "상태": "신규", "내 코멘트": "",
         "첫 포착": "2026-11-09", "최근 포착": "2026-11-09"},
        {S.KEY: "nps:123456:지신", "회사명": "지신", "점수": 82, "최근 포착": "2026-11-09"},
    ])
    h = grid[0]
    row = dict(zip(h, grid[1]))
    assert row["상태"] == "관심" and row["내 코멘트"] == "광고비 비중 확인"   # user cols untouched
    assert row["점수"] == "84" and row["첫 포착"] == "2026-11-05"           # system cols refreshed, first-seen kept
    assert row["내 메모(사용자 추가)"] == "따로 적은 것"                      # user's extra column survives
    new = dict(zip(h, grid[2]))
    assert new["상태"] == "신규" and new["첫 포착"] == "2026-11-09"


def test_snapshot_and_lens_markdown():
    cand = [S.CAND_COLS, ["dart:1", "딥다이브", "패스", "-이미 큼", "", *[""] * (len(S.CAND_COLS) - 5)]]
    lens = [S.LENS_COLS, ["2026-11-05", "직접", "B2B 반복매출이면 인원이 적어도 본다", "", ""],
            ["2026-11-05", "Claude", "얼리 스테이지", "사용자 메시지", "가중치"], ["", "", "", "", ""]]
    snap = S.snapshot(cand, lens)
    assert snap["candidates"][0]["상태"] == "패스" and len(snap["lens"]) == 2
    md = S.lens_markdown(snap)
    assert "B2B 반복매출" in md and md.index("최우선") < md.index("Claude가 피드백")
