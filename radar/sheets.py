"""Google Sheets as the user's working surface (replaces the Notion DB).

All sheet I/O happens in GitHub Actions with a service account
(secret GOOGLE_SA_JSON, sheet shared with the service-account e-mail as editor).
The Claude morning judgment never talks to Sheets directly: it reads the
snapshot this module exports into the repo and writes an outbox file that
this module applies.

Tabs
  후보      one row per company the judgment graded A or B. User-owned columns
            (상태 / 이유 태그 / 내 코멘트) are never overwritten.
  관점      the user's investment lens. '직접' rows are the user's,
            'Claude' rows are appended by the judgment with evidence.
  판단 로그  every judged company with its grade A–F and the reason.

Commands
  python -m radar.sheets pull   sheet -> data/feedback/sheet_snapshot.json, data/feedback/lens.md
  python -m radar.sheets push   data/sheet/outbox/*.json -> sheet, then archive the outbox
  python -m radar.sheets init   create tabs, headers, dropdowns (idempotent)
"""
from __future__ import annotations

import datetime as dt
import json
import re
import os
import sys
from pathlib import Path

from .store import DATA

SHEET_ID_DEFAULT = "1vPTO0HS4ugVo04y2yQRmXMdxof8tx00qClo0ZB-UXGk"

CAND_TAB, LENS_TAB, LOG_TAB, NOTE_TAB = "후보", "관점", "판단 로그", "심사 노트"
KEY = "레이더 키"
USER_COLS = ["상태", "이유 태그", "내 코멘트"]
REG = "후보 등록일"  # 판단에서 처음 A·B를 받아 후보 탭에 올라간 날 (한 번 정해지면 바뀌지 않음)
SUMMARY, NOTE_LINK = "한 줄 요약", "심사 노트"
CAND_COLS = [REG, KEY, "회사명", SUMMARY, NOTE_LINK, "등급", *USER_COLS, "점수", "소스", "업력(년)", "고용(명)",
             "고용 12개월(%)", "매출(억, 추정)", "영업이익률(%)", "외부투자", "왜 지금",
             "반박 검증", "점수 사유", "근거 링크", "홈페이지", "첫 포착", "최근 포착"]
LENS_COLS = ["날짜", "누가", "관점", "근거", "적용"]
LOG_COLS = ["날짜", KEY, "회사명", SUMMARY, "등급", "판정", "이유", "근거"]
# 심사 노트: 판단 때 조사한 내용을 회사마다 고정 양식으로 남긴다 (휘발 방지). 각 칸은 '· ' 줄 단위.
NOTE_FIELDS = [SUMMARY, "회사 개요", "사업·제품", "고객·수익 구조", "팀·창업자", "트랙션", "시장·산업",
               "경쟁·대안", "왜 지금", "좋은 투자인가", "리스크·사전 부검", "컨택 시 확인할 것", "출처"]
NOTE_COLS = [KEY, "회사명", "등급", "조사일", *NOTE_FIELDS, "노트 상태"]
GRADES = ["A", "B", "C", "D", "E", "F"]
STATUS = ["신규", "관찰", "관심", "컨택", "패스"]
TAGS = ["+수익구조", "+시장", "+창업자·팀", "+해자", "+타이밍",
        "-이미 큼", "-투자받음", "-계열·이전", "-테제 밖", "-사업모델", "-인력 의존", "-데이터 오류"]

OUTBOX = DATA / "sheet" / "outbox"
NOTES_DIR = DATA / "notes"   # 회사별 심사 노트 원본 (시트는 보기용). 다음 판단이 다시 읽는다
SNAPSHOT = DATA / "feedback" / "sheet_snapshot.json"
LENS_MD = DATA / "feedback" / "lens.md"


# ------------------------------------------------------------ pure logic (tested)

def merge_candidates(existing: list[list[str]], upserts: list[dict],
                     registered: dict[str, str] | None = None, today: str = "") -> list[list[str]]:
    """Return the full 후보 grid (header + rows) after upserting.

    Rows are matched by 레이더 키. For an existing row only system columns are
    refreshed; the user's columns keep whatever the user typed. New rows get
    상태 '신규'. Unknown extra columns the user added are preserved in place.
    """
    header = list(existing[0]) if existing else list(CAND_COLS)
    rows = [list(r) + [""] * (len(header) - len(r)) for r in existing[1:]]
    for i, col in enumerate(CAND_COLS):  # tolerate a user re-ordering or adding columns
        if col not in header:
            # a new system column goes right after its predecessor so the layout stays readable
            prev = next((header.index(p) for p in reversed(CAND_COLS[:i]) if p in header), -1)
            header.insert(prev + 1, col)
            for r in rows:
                r.insert(prev + 1, "")
    idx = {c: i for i, c in enumerate(header)}
    by_key = {r[idx[KEY]]: r for r in rows if r[idx[KEY]]}
    for k, row in by_key.items():  # rows from before the column existed: date of the first A·B
        if not row[idx[REG]] and (registered or {}).get(k):
            row[idx[REG]] = registered[k]
    for u in upserts:
        k = str(u.get(KEY) or "").strip()
        if not k:
            continue
        row = by_key.get(k)
        if row is None:
            row = [""] * len(header)
            row[idx[KEY]] = k
            row[idx["상태"]] = "신규"
            row[idx[REG]] = str(u.get(REG) or (registered or {}).get(k) or today)
            row[idx["첫 포착"]] = str(u.get("첫 포착") or u.get("최근 포착") or "")
            rows.append(row)
            by_key[k] = row
        for col, val in u.items():
            if col in USER_COLS or col == KEY or col not in idx:
                continue
            if col == REG or (col == "첫 포착" and row[idx[col]]):
                continue
            row[idx[col]] = "" if val is None else str(val)
    return [header] + rows


def registration_dates(folder: Path | None = None) -> dict[str, str]:
    """레이더 키 -> first judgment date that graded it A or B (data/judgments/*.md)."""
    from .pipeline import _KEY, _grade_of
    folder = folder or DATA / "judgments"
    out: dict[str, str] = {}
    for f in sorted(folder.glob("*.md")) if folder.exists() else []:
        date, section = f.stem[:10], ""
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                section = line[3:].strip()
                continue
            m = _KEY.match(line.strip())
            if m and _grade_of(line, section) in ("A", "B"):
                out.setdefault(m.group(1), date)
    return out


def note_path(key: str, folder: Path | None = None) -> Path:
    return (folder or NOTES_DIR) / (re.sub(r"[^\w가-힣.-]+", "_", key) + ".json")


def merge_note(old: dict | None, new: dict) -> dict:
    """Latest research wins field by field; empty fields keep what we knew. History of
    (조사일, 등급) is kept so a re-judgment shows how the view changed."""
    out = dict(old or {})
    for k, v in new.items():
        if k == "이력":
            continue
        if v not in (None, "", []):
            out[k] = v
    hist = list((old or {}).get("이력", []))
    stamp = {"조사일": new.get("조사일", ""), "등급": new.get("등급", "")}
    if any(stamp.values()) and stamp not in hist:
        hist.append(stamp)
    out["이력"] = hist
    return out


def upsert_grid(existing: list[list[str]], cols: list[str], records: list[dict]) -> list[list[str]]:
    """Generic upsert by 레이더 키 (no user-owned columns). Missing columns are inserted in place."""
    header = list(existing[0]) if existing else list(cols)
    rows = [list(r) + [""] * (len(header) - len(r)) for r in existing[1:]]
    for i, col in enumerate(cols):
        if col not in header:
            prev = next((header.index(p) for p in reversed(cols[:i]) if p in header), -1)
            header.insert(prev + 1, col)
            for r in rows:
                r.insert(prev + 1, "")
    idx = {c: i for i, c in enumerate(header)}
    by_key = {r[idx[KEY]]: r for r in rows if r[idx[KEY]]}
    for rec in records:
        k = str(rec.get(KEY) or "").strip()
        if not k:
            continue
        row = by_key.get(k)
        if row is None:
            row = [""] * len(header)
            rows.append(row)
            by_key[k] = row
        for col, val in rec.items():
            if col in idx:
                row[idx[col]] = "" if val is None else str(val)
    return [header] + rows


def note_link(gid: int, key: str) -> str:
    """Jump to the company's row in 심사 노트 (MATCH keeps working if the user sorts)."""
    return (f'=HYPERLINK("#gid={gid}&range=A"&MATCH("{key}",\'{NOTE_TAB}\'!A:A,0),"노트 보기")')


def with_links(grid: list[list[str]], gid: int, note_keys: set[str]) -> list[list[str]]:
    """Re-apply link formulas (a values read returns the display text, which would overwrite them)."""
    if not grid or NOTE_LINK not in grid[0]:
        return grid
    h = grid[0]
    ki, li = h.index(KEY), h.index(NOTE_LINK)
    for r in grid[1:]:
        if len(r) > max(ki, li) and r[ki] in note_keys:
            r[li] = note_link(gid, r[ki])
    return grid


def snapshot(cand_grid: list[list[str]], lens_grid: list[list[str]]) -> dict:
    """User-side state the judgment needs: every row the user touched + the lens."""
    out = {"pulled_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "candidates": [], "lens": []}
    if cand_grid:
        h = cand_grid[0]
        for r in cand_grid[1:]:
            rec = {h[i]: (r[i] if i < len(r) else "") for i in range(len(h))}
            if rec.get(KEY):
                out["candidates"].append(rec)
    if lens_grid:
        h = lens_grid[0]
        for r in lens_grid[1:]:
            rec = {h[i]: (r[i] if i < len(r) else "") for i in range(len(h))}
            if any(v.strip() for v in rec.values()):
                out["lens"].append(rec)
    return out


def only_listable(existing: list[list[str]], upserts: list[dict]) -> list[dict]:
    """New rows only for grade A/B (no grade = legacy = B); rows already in the sheet are
    always updated, so a company that drops to C or below shows its new grade."""
    have = set()
    if existing:
        k = existing[0].index(KEY) if KEY in existing[0] else 0
        have = {r[k] for r in existing[1:] if len(r) > k and r[k]}
    return [u for u in upserts
            if str(u.get(KEY) or "").strip() in have or str(u.get("등급") or "B").strip()[:1] in ("A", "B")]


def align_rows(header: list[str], records: list[dict]) -> tuple[list[str], list[list[str]]]:
    """Rows ordered by the sheet's actual header; columns the sheet lacks are appended to it."""
    header = list(header)
    for r in records:
        for c in r:
            if c not in header:
                header.append(c)
    return header, [["" if r.get(c) is None else str(r.get(c)) for c in header] for r in records]


def lens_markdown(snap: dict) -> str:
    lines = ["# 내 투자 관점 (구글시트 '관점' 탭에서 가져옴)", ""]
    for who, title in (("직접", "사용자가 직접 쓴 관점 · 참고 사례 (최우선)"),
                       ("Claude", "Claude가 피드백에서 정리한 관점")):
        rows = [r for r in snap["lens"] if r.get("누가", "").startswith(who)]
        lines += [f"## {title}", ""]
        lines += [f"- {r.get('날짜', '')} · {r.get('관점', '')}"
                  + (f" · 근거: {r['근거']}" if r.get("근거") else "")
                  + (f" · 적용: {r['적용']}" if r.get("적용") else "") for r in rows] or ["- (없음)"]
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------ sheet I/O

def _open():
    import gspread
    from google.oauth2.service_account import Credentials
    info = json.loads(os.environ["GOOGLE_SA_JSON"])
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds).open_by_key(os.environ.get("SHEET_ID") or SHEET_ID_DEFAULT)


def _tab(sh, name: str, cols: list[str]):
    import gspread
    try:
        ws = sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(name, rows=200, cols=max(len(cols), 10))
    if not ws.row_values(1):
        ws.update([cols], "A1")
        ws.freeze(rows=1)
    return ws


def _note_tab(sh):
    import gspread
    try:
        return sh.worksheet(NOTE_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(NOTE_TAB, rows=200, cols=len(NOTE_COLS))
    ws.update([NOTE_COLS], "A1")
    n = len(NOTE_COLS)
    widths = [170, 150, 50, 90] + [330] * len(NOTE_FIELDS) + [150]
    reqs = [{"updateSheetProperties": {"properties": {"sheetId": ws.id, "gridProperties": {
                "frozenRowCount": 1, "frozenColumnCount": 2}},
             "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
            {"repeatCell": {"range": {"sheetId": ws.id, "startColumnIndex": 0, "endColumnIndex": n},
                            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
                            "fields": "userEnteredFormat.wrapStrategy,userEnteredFormat.verticalAlignment"}},
            {"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1},
                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                     "backgroundColorStyle": {"rgbColor": {"red": .93, "green": .93, "blue": .93}}}},
                            "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.backgroundColorStyle"}}]
    reqs += [{"updateDimensionProperties": {"range": {"sheetId": ws.id, "dimension": "COLUMNS",
                                                      "startIndex": i, "endIndex": i + 1},
                                            "properties": {"pixelSize": w}, "fields": "pixelSize"}}
             for i, w in enumerate(widths)]
    sh.batch_update({"requests": reqs})
    return ws


def _note_keys(note_ws) -> set[str]:
    return {k for k in note_ws.col_values(1)[1:] if k}


def init() -> None:
    sh = _open()
    cand = _tab(sh, CAND_TAB, CAND_COLS)
    lens = _tab(sh, LENS_TAB, LENS_COLS)
    _tab(sh, LOG_TAB, LOG_COLS)
    note_ws = _note_tab(sh)
    # new system columns are inserted physically (cells, dropdowns and formats shift with them)
    h = cand.row_values(1)
    for i, c in enumerate(CAND_COLS):
        if c not in h:
            pos = next((h.index(p) + 1 for p in reversed(CAND_COLS[:i]) if p in h), 0)
            cand.insert_cols([[c]], col=pos + 1, value_input_option="RAW")
            h.insert(pos, c)
            if c in (SUMMARY, NOTE_LINK):
                sh.batch_update({"requests": [{"updateDimensionProperties": {
                    "range": {"sheetId": cand.id, "dimension": "COLUMNS", "startIndex": pos, "endIndex": pos + 1},
                    "properties": {"pixelSize": 380 if c == SUMMARY else 80}, "fields": "pixelSize"}}]})
    grid = cand.get_all_values()
    filled = merge_candidates(grid, [], registration_dates())
    if filled != [list(r) + [""] * (len(filled[0]) - len(r)) for r in grid]:
        cand.update(with_links(filled, note_ws.id, _note_keys(note_ws)), "A1", value_input_option="USER_ENTERED")
        print("init: 후보 등록일 backfilled")
    # dropdown for 상태; 이유 태그 is free text (comma-separated) listed in the note
    h = cand.row_values(1)
    col = h.index("상태") + 1
    sh.batch_update({"requests": [{  # columns may have shifted (e.g. 등급 inserted): clear old dropdowns
        "setDataValidation": {"range": {"sheetId": cand.id, "startRowIndex": 1,
                                        "startColumnIndex": 0, "endColumnIndex": len(h)}}}]})
    try:
        cand.clear_notes([f"A1:{chr(64 + min(len(h), 26))}1"])
    except Exception:  # older gspread
        pass
    sh.batch_update({"requests": [{
        "setDataValidation": {
            "range": {"sheetId": cand.id, "startRowIndex": 1, "startColumnIndex": col - 1,
                      "endColumnIndex": col},
            "rule": {"condition": {"type": "ONE_OF_LIST",
                                   "values": [{"userEnteredValue": s} for s in STATUS]},
                     "showCustomUi": True, "strict": False}}}]})
    tcol = h.index("이유 태그") + 1
    cand.update_note(f"{chr(64 + tcol)}1", "쉼표로 여러 개: " + ", ".join(TAGS))
    if len(lens.get_all_values()) <= 1:
        today = dt.date.today().isoformat()
        lens.append_rows([
            [today, "직접", "(여기에 한 줄씩 자유롭게. 예: B2B 반복매출이면 인원이 적어도 본다)", "", ""],
            [today, "직접 · 사례(원함)", "방탈출 체인, 업력 5년 내 매출 50억 수준까지 외부투자 없이 성장", "", ""],
            [today, "직접 · 사례(원함)", "다품종 소량 식품 OEM, 5~6년에 매출 300억", "", ""],
            [today, "Claude", "매출이 백억 단위일 필요 없다. 더 얼리 스테이지를 본다",
             "첫 다이제스트 후 사용자 메시지", "업력 5년 이하·팀 10~80명 고용 성장 주력, 매출 500억+/300명+ 감점"],
            [today, "Claude", "젊은 법인이라도 기존 사업 이전·대기업 계열·분할·외국계 한국법인은 아니다",
             "첫 판단 20건 중 15건이 이 이유로 탈락", "설립 첫해 대형 매출·한 달 몰린 고용 증가 감점, 판단 탈락 기준"],
            [today, "Claude", "외부 VC 투자를 받은 회사는 제외한다", "최초 요구사항", "판단 탈락 기준"],
        ], value_input_option="RAW")
    print("init ok:", [w.title for w in sh.worksheets()])


def pull() -> dict:
    sh = _open()
    cand = _tab(sh, CAND_TAB, CAND_COLS).get_all_values()
    lens = _tab(sh, LENS_TAB, LENS_COLS).get_all_values()
    snap = snapshot(cand, lens)
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    LENS_MD.write_text(lens_markdown(snap), encoding="utf-8")
    print(f"pull ok: {len(snap['candidates'])} rows, {len(snap['lens'])} lens lines")
    return snap


def push() -> None:
    files = sorted(OUTBOX.glob("*.json")) if OUTBOX.exists() else []
    if not files:
        print("push: outbox empty")
        return
    sh = _open()
    cand_ws = _tab(sh, CAND_TAB, CAND_COLS)
    lens_ws = _tab(sh, LENS_TAB, LENS_COLS)
    log_ws = _tab(sh, LOG_TAB, LOG_COLS)
    note_ws = _note_tab(sh)
    upserts, lens_add, log_add, notes = [], [], [], []
    for f in files:
        box = json.loads(f.read_text(encoding="utf-8"))
        upserts += box.get("candidates", [])
        notes += box.get("notes", [])
        lens_add += [[r.get(c, "") for c in LENS_COLS] for r in box.get("lens", [])]
        log_add += box.get("log", [])
    existing = cand_ws.get_all_values() if (upserts or notes) else []
    if notes:
        # 원본은 repo(data/notes)에 누적 저장, 시트 '심사 노트'에는 후보(A·B)와 이미 시트에 있는 회사만
        in_cand = {r[existing[0].index(KEY)] for r in existing[1:]} if existing else set()
        listed = {str(u.get(KEY)) for u in only_listable(existing, upserts)}
        on_sheet = _note_keys(note_ws)
        rows = []
        for n in notes:
            k = str(n.get(KEY) or "").strip()
            if not k:
                continue
            path = note_path(k)
            path.parent.mkdir(parents=True, exist_ok=True)
            merged = merge_note(json.loads(path.read_text(encoding="utf-8")) if path.exists() else None, n)
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
            if k in in_cand or k in listed or k in on_sheet or str(merged.get("등급", ""))[:1] in ("A", "B"):
                rows.append({c: merged.get(c, "") for c in NOTE_COLS})
                if merged.get(SUMMARY) and (k in in_cand or k in listed):
                    upserts.append({KEY: k, SUMMARY: merged[SUMMARY]})
        if rows:
            ngrid = upsert_grid(note_ws.get_all_values(), NOTE_COLS, rows)
            note_ws.resize(rows=max(len(ngrid) + 50, note_ws.row_count), cols=max(len(ngrid[0]), note_ws.col_count))
            note_ws.update(ngrid, "A1", value_input_option="RAW")
    if upserts:
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date().isoformat()
        grid = merge_candidates(existing, only_listable(existing, upserts), registration_dates(), today)
        grid = with_links(grid, note_ws.id, _note_keys(note_ws))
        cand_ws.resize(rows=max(len(grid) + 50, cand_ws.row_count), cols=max(len(grid[0]), cand_ws.col_count))
        cand_ws.update(grid, "A1", value_input_option="USER_ENTERED")
    if lens_add:
        lens_ws.append_rows(lens_add, value_input_option="RAW")
    if log_add:
        old = log_ws.row_values(1) or LOG_COLS
        header, rows = align_rows(old, log_add)
        if header != old:
            log_ws.resize(cols=max(len(header), log_ws.col_count))
            log_ws.update([header], "A1")
        log_ws.append_rows(rows, value_input_option="RAW")
    done = OUTBOX.parent / "sent"
    done.mkdir(parents=True, exist_ok=True)
    for f in files:
        f.rename(done / f.name)
    print(f"push ok: {len(upserts)} candidates, {len(notes)} notes, {len(lens_add)} lens, {len(log_add)} log rows")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "pull"
    {"init": init, "pull": pull, "push": push}[cmd]()
