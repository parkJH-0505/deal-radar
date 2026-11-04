"""Company identity: name normalisation and cross-source matching.

Sources disagree on identifiers:
- DART: corp_code + full 10-digit business number (bizr_no)
- 국민연금: business number masked to first 6 digits + workplace name
- 인허가: no business number at all, only site name + address
So matching is name-led, tightened by bizno prefix or region when available.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_LEGAL_FORMS = [
    "주식회사", "유한회사", "유한책임회사", "합자회사", "합명회사",
    "농업회사법인", "영농조합법인", "어업회사법인", "영어조합법인",
    "사단법인", "재단법인", "협동조합",
]
_PAREN_FORMS = re.compile(r"[\(（]\s*(주|유|합|사|재|농|영)\s*[\)）]|㈜|㈔|㈕")
# Branch suffixes are removed only when clearly a site label, not part of the
# brand: "해담식품 제2공장" / "해담식품 음성공장" / "해담식품지점" -> "해담식품",
# but "맛있는공장" (a brand) stays intact.
_BRANCH_NUMBERED = re.compile(r"\s*(제\s*)?\d+\s*(공장|지점|사업장|센터)\s*$")
_BRANCH_SPACED = re.compile(r"\s+\S{0,6}(공장|지점|사업장|센터|본점|본사|물류센터)\s*$")
_BRANCH_ALWAYS = re.compile(r"(지점|본점|사업장)\s*$")
_NONWORD = re.compile(r"[^0-9a-z가-힣]")


def norm_name(name: str | None, *, strip_branch: bool = True) -> str:
    if not name:
        return ""
    s = str(name).strip().lower()
    s = _PAREN_FORMS.sub(" ", s)
    for f in _LEGAL_FORMS:
        s = s.replace(f, " ")
    s = re.sub(r"\s+", " ", s).strip()
    if strip_branch:
        for rx in (_BRANCH_NUMBERED, _BRANCH_SPACED, _BRANCH_ALWAYS):
            t = rx.sub("", s).strip()
            if len(_NONWORD.sub("", t)) >= 2:  # never strip down to nothing
                s = t
    return _NONWORD.sub("", s)


def name_similarity(a: str, b: str) -> float:
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9 if min(len(na), len(nb)) >= 3 else 0.6
    return SequenceMatcher(None, na, nb).ratio()


def bizno6(bizno: str | None) -> str:
    d = re.sub(r"\D", "", str(bizno or ""))
    return d[:6] if len(d) >= 6 else ""


_SIDO = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기",
         "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]
_SIDO_LONG = {
    "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북",
    "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "강원특별자치도": "강원",
    "강원도": "강원", "제주특별자치도": "제주", "경기도": "경기", "서울특별시": "서울",
    "세종특별자치시": "세종",
}


def sido(addr: str | None) -> str:
    if not addr:
        return ""
    a = str(addr).strip()
    for long, short in _SIDO_LONG.items():
        if a.startswith(long):
            return short
    for s in _SIDO:
        if a.startswith(s):
            return s
    return ""
