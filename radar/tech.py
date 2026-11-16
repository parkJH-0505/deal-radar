"""Technology-based young companies (B2B SW, AI/data, semiconductors, robotics, bio, devices).

Why a separate lane: the 국민연금 growth scan ranks all industries together, and
consumer/food/service SMEs dominate it by sheer number, while 인허가·식품안전나라
only ever surface consumer/food businesses. Tech startups that have NOT yet raised
VC money are usually tiny (5-15 people) and under 3 years old, so they need their
own, earlier thresholds to be seen before the first institutional round.

Industry is matched on the 국민연금 업종명 (the code edition differs from DART's KSIC).
"""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=8)
def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def industry_of(c: dict) -> str:
    return c.get("industry") or (c.get("nps") or {}).get("industry") or ""


def is_tech(industry: str, cfg: dict) -> bool:
    t = cfg.get("tech") or {}
    if not industry or not t.get("industry_include"):
        return False
    if t.get("industry_exclude") and _rx(t["industry_exclude"]).search(industry):
        return False
    return bool(_rx(t["industry_include"]).search(industry))


def is_noise_service(industry: str, cfg: dict) -> bool:
    """Professional/care/tutoring practices: grow by adding staff, not startups."""
    p = (cfg.get("global") or {}).get("exclude_industry_names")
    return bool(p and industry and _rx(p).search(industry))
