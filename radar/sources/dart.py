"""OpenDART: 외부감사(F) 공시를 이용한 '처음 외감 대상이 된 회사' 탐지.

외감법상 직전 사업연도 기준 자산 120억 / 부채 70억 / 매출 100억 / 종업원 100명
중 2개 이상(또는 자산·매출 500억 이상)이면 감사보고서가 DART에 올라온다.
=> 비상장(corp_cls=E) 회사가 '처음' 감사보고서를 낸 해 = 대략 100억 고개를 넘은 해.

Index file (sorted text, git-friendly diffs):
  data/state/dart_audit_seen.tsv
  corp_code \t first_rcept_dt \t corp_name \t last_rcept_dt \t last_rcept_no
"""
from __future__ import annotations

import datetime as dt
import io
import re
import zipfile
from pathlib import Path

from ..http import HttpError, get, log

BASE = "https://opendart.fss.or.kr/api"
VIEW = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


class DartError(RuntimeError):
    pass


def _call(key: str, path: str, params: dict) -> dict:
    p = {"crtfc_key": key, **params}
    j = get(f"{BASE}/{path}", p, pause=0.05).json()
    st = j.get("status")
    if st in ("000", "013"):  # ok / no data
        return j
    if st == "020":
        raise DartError("DART 일일 호출 한도 초과 (020)")
    raise DartError(f"DART {path} status={st} msg={j.get('message')}")


def list_filings(key: str, bgn: str, end: str, *, pblntf_ty: str = "F",
                 corp_cls: str | None = "E", corp_code: str | None = None) -> list[dict]:
    out, page = [], 1
    while True:
        params = {"bgn_de": bgn, "end_de": end, "pblntf_ty": pblntf_ty,
                  "page_no": page, "page_count": 100}
        if corp_cls:
            params["corp_cls"] = corp_cls
        if corp_code:
            params["corp_code"] = corp_code
        j = _call(key, "list.json", params)
        if j.get("status") == "013":
            break
        out.extend(j.get("list", []))
        if page >= int(j.get("total_page", 1) or 1):
            break
        page += 1
    return out


def is_audit_report(f: dict) -> bool:
    nm = f.get("report_nm", "")
    return "감사보고서" in nm and "검토" not in nm


def month_windows(start: dt.date, end: dt.date):
    cur = start.replace(day=1)
    while cur <= end:
        nxt = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield max(cur, start), min(nxt - dt.timedelta(days=1), end)
        cur = nxt


# ------------------------------------------------------------------ index

class SeenIndex:
    def __init__(self, path: Path):
        self.path = path
        self.first: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.last: dict[str, tuple[str, str]] = {}  # corp -> (rcept_dt, rcept_no)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                p = line.split("\t")
                if len(p) >= 2:
                    self.first[p[0]] = p[1]
                    self.names[p[0]] = p[2] if len(p) > 2 else ""
                    if len(p) >= 5:
                        self.last[p[0]] = (p[3], p[4])

    def __len__(self) -> int:
        return len(self.first)

    def add(self, f: dict) -> bool:
        """Register an audit filing. Returns True if the corp is new to the index."""
        code, d, no = f["corp_code"], f["rcept_dt"], f.get("rcept_no", "")
        prev = self.first.get(code)
        self.names[code] = (f.get("corp_name") or self.names.get(code, "")).replace("\t", " ")
        if prev is None or d < prev:
            self.first[code] = d
        if code not in self.last or d >= self.last[code][0]:
            self.last[code] = (d, no)
        return prev is None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for c, d in sorted(self.first.items()):
            ld, ln = self.last.get(c, ("", ""))
            lines.append(f"{c}\t{d}\t{self.names.get(c, '')}\t{ld}\t{ln}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def backfill(key: str, index: SeenIndex, start: dt.date, end: dt.date) -> int:
    n = 0
    for a, b in month_windows(start, end):
        fl = list_filings(key, a.strftime("%Y%m%d"), b.strftime("%Y%m%d"))
        aud = [f for f in fl if is_audit_report(f)]
        for f in aud:
            index.add(f)
        n += len(aud)
        log(f"[dart] {a}~{b}: {len(aud)} audit reports (index={len(index.first)})")
    return n


# ---------------------------------------------------------------- company

def company(key: str, corp_code: str) -> dict | None:
    j = _call(key, "company.json", {"corp_code": corp_code})
    return j if j.get("status") == "000" else None


# ------------------------------------------------------- audit financials

def audit_text(key: str, rcept_no: str) -> str:
    try:
        r = get(f"{BASE}/document.xml", {"crtfc_key": key, "rcept_no": rcept_no},
                timeout=120)
    except HttpError as e:
        log(f"[dart] document {rcept_no} failed: {e}")
        return ""
    try:
        zf = zipfile.ZipFile(io.BytesIO(r.content))
    except zipfile.BadZipFile:
        return ""
    texts = []
    for name in zf.namelist():
        raw = zf.read(name)
        for enc in ("utf-8", "euc-kr", "cp949"):
            try:
                texts.append(raw.decode(enc))
                break
            except UnicodeDecodeError:
                continue
    xml = "\n".join(texts)
    xml = re.sub(r"<(TD|TE|TU|TH)[^>]*>", " | ", xml, flags=re.I)
    xml = re.sub(r"</?(TR|P|BR)[^>]*>", "\n", xml, flags=re.I)
    return re.sub(r"<[^>]+>", " ", xml)


_NUM = r"\(?-?[\d]{1,3}(?:,\d{3})+\)?|\(?-?\d{5,}\)?"
_UNIT = re.compile(r"단\s*위\s*[:：]\s*(백만원|천원|원)")
_ROWS = {
    "revenue": re.compile(r"(매\s*출\s*액|영\s*업\s*수\s*익|수\s*익\s*\(\s*매\s*출\s*액\s*\))"),
    "op_income": re.compile(r"영\s*업\s*(이\s*익|손\s*실|손\s*익)"),
    "net_income": re.compile(r"당\s*기\s*순\s*(이\s*익|손\s*실|손\s*익)"),
}
_MULT = {"원": 1, "천원": 1_000, "백만원": 1_000_000}


def _num(s: str) -> int:
    neg = s.startswith("(") or s.startswith("-")
    v = int(re.sub(r"\D", "", s))
    return -v if neg else v


def extract_financials(text: str) -> dict:
    """Best-effort pull of 당기/전기 매출·영업이익 from the 손익계산서.

    Confidence is 'low' by design: the judgment step re-reads the filing.
    """
    if not text:
        return {}
    start = None
    for kw in ("포괄손익계산서", "손익계산서"):
        m = re.search(r"\s*".join(kw), text)
        if m:
            start = m.start()
            break
    if start is None:
        return {}
    seg = text[start:start + 30000]
    um = _UNIT.search(text[max(0, start - 500):start + 3000])
    unit = um.group(1) if um else "원"
    mult = _MULT[unit]
    out: dict = {"unit": unit, "confidence": "low"}
    for field, rx in _ROWS.items():
        m = rx.search(seg)
        if not m:
            continue
        tail = seg[m.end():m.end() + 400]
        tail = tail.split("\n")[0] if "\n" in tail[:200] else tail
        nums = re.findall(_NUM, tail)
        if not nums:
            continue
        vals = [_num(n) * mult for n in nums[:2]]
        if field == "op_income" and "손" in m.group(1) and "익" not in m.group(1):
            vals = [-abs(v) for v in vals]
        out[field] = vals[0]
        if len(vals) > 1:
            out[f"{field}_prev"] = vals[1]
    return out if "revenue" in out else {}
