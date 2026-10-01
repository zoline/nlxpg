"""영문 컬럼명 → 공통표준단어 추정.

BIRD·dvdrental처럼 한글 COMMENT 없이 영문 이름만 있는 스키마는 표준 검증에서 전부
"판정 불가"였다(2026-10-01, dvdrental 86개 컬럼 모두). 공통표준단어의 **영문명(Full Name)**,
영문약어, 그리고 DB에서 흔히 쓰는 관용 영문으로 컬럼 이름을 표준단어 열로 바꾼다.

`first_name` → [최초, 명], `customer_id` → [고객, 아이디], `last_update` → [최종, 갱신].
**추정**이다 — 영어 한 단어가 여러 한국어 단어에 대응하고(release: 공개·발매·불출),
같은 뜻이라도 표준이 고른 영문이 다르다(create ↔ Creation). 결과에는 항상 추정임을 표시한다.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from nlxpg.standards.loader import Standards

#: DB 컬럼 이름에서 흔한 영문(관용어·약어) → 표준단어명. 표준단어의 영문명이 DB 관례와
#: 다른 것만 둔다(예: 일자의 영문명은 'YEAR Month Day'라 'date'로는 찾을 수 없다).
#: 매뉴얼 120쪽 "관용적으로 쓰는 영문약어는 관용어 기준을 적용" 취지를 따른다.
DB_ALIASES: dict[str, str] = {
    "date": "일자", "dt": "일시", "datetime": "일시", "timestamp": "일시", "ts": "일시",
    "time": "시각", "tm": "시각",
    "phone": "전화번호", "tel": "전화번호", "telephone": "전화번호", "mobile": "휴대전화번호",
    "no": "번호", "num": "번호", "number": "번호", "nbr": "번호",
    "cd": "코드", "code": "코드",
    "name": "명", "nm": "명", "title": "제목",
    "yn": "여부", "flag": "여부", "flg": "여부",
    "cnt": "수", "count": "수", "qty": "수량", "amt": "금액",
    "desc": "설명", "description": "설명", "seq": "일련번호", "sn": "일련번호",
    "create": "생성", "created": "생성", "crt": "생성",
    "update": "갱신", "updated": "갱신", "upd": "갱신",
    "modify": "수정", "modified": "수정", "delete": "삭제", "deleted": "삭제",
    "user": "사용자", "active": "활성", "status": "상태", "type": "유형",
    "country": "국가", "photo": "사진", "picture": "사진", "postal": "우편번호",
    "email": "이메일", "mail": "이메일", "year": "연도", "price": "가격", "employee": "직원",
    "first": "최초", "last": "최종", "content": "내용", "reg": "등록",
}
#: 여러 토큰이 한 단어인 관용 표현
DB_PHRASES: dict[str, str] = {"postal code": "우편번호", "zip code": "우편번호", "e mail": "이메일"}

_MAX_SPAN = 4
_SPLIT = re.compile(r"[^0-9A-Za-z]+|(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")


@dataclass
class EnglishIndex:
    phrases: dict[str, list[str]]   # 정규화한 영문명 → 표준단어들 (많이 쓰는 순)
    abbrs: dict[str, str]           # 영문약어(소문자) → 표준단어
    freq: Counter[str]              # 표준단어 → 표준용어 영문약어에 나온 횟수


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


def build_index(std: Standards) -> EnglishIndex:
    cached = getattr(std, "_english_index", None)
    if cached is not None:
        return cached
    by_abbr: dict[str, str] = {}
    for w in std.words.values():
        by_abbr.setdefault(w.abbr.lower(), w.name)
    freq: Counter[str] = Counter()
    for t in std.terms.values():
        for part in t.abbr.lower().split("_"):
            if part in by_abbr:
                freq[by_abbr[part]] += 1
    phrases: dict[str, list[str]] = {}
    for w in std.words.values():
        if w.english:
            phrases.setdefault(_norm(w.english), []).append(w.name)
    for k, v in phrases.items():
        v.sort(key=lambda name: -freq[name])
    for k, v in DB_PHRASES.items():
        if v in std.words:
            phrases[k] = [v]
    for k, v in DB_ALIASES.items():
        if v in std.words:
            phrases[k] = [v]  # 관용어가 표준 영문명보다 우선 (title → 제목, 'Title'이 없어도)
    idx = EnglishIndex(phrases, by_abbr, freq)
    object.__setattr__(std, "_english_index", idx)
    return idx


def tokens(name: str) -> list[str]:
    return [t.lower() for t in _SPLIT.split(name) if t and not t.isdigit()]


def _variants(tok: str) -> list[str]:
    """복수형·과거형 등 간단한 활용형. create → creation 처럼 표준이 명사형을 쓴 경우도 본다."""
    out = [tok]
    if tok.endswith("ies"):
        out.append(tok[:-3] + "y")
    if tok.endswith("es"):
        out.append(tok[:-2])
    if tok.endswith("s") and not tok.endswith("ss"):
        out.append(tok[:-1])
    if tok.endswith("ed"):
        out += [tok[:-2], tok[:-1]]
    if tok.endswith("e"):
        out.append(tok[:-1] + "ion")      # create → creation
    out.append(tok + "ion")
    return list(dict.fromkeys(out))


def _lookup(idx: EnglishIndex, toks: list[str]) -> str | None:
    if len(toks) == 1:
        tok = toks[0]
        for v in _variants(tok):
            if v in idx.phrases:
                return idx.phrases[v][0]
        return idx.abbrs.get(tok)
    head = " ".join(toks[:-1])
    for v in _variants(toks[-1]):
        key = f"{head} {v}"
        if key in idx.phrases:
            return idx.phrases[key][0]
    return None


def infer_words(std: Standards, column: str) -> tuple[list[str], list[str]] | None:
    """영문 컬럼명 → (표준단어 열, 원래 토큰 묶음). 토큰을 모두 덮지 못하면 None.
    묶음 수가 가장 적은 분할을 고르고(복합어 우선, 매뉴얼 105쪽), 같으면 많이 쓰는 단어 쪽."""
    idx = build_index(std)
    toks = tokens(column)
    if not toks:
        return None
    n = len(toks)
    best: list[tuple[int, int, list[str], list[str]] | None] = [None] * (n + 1)
    best[0] = (0, 0, [], [])
    for i in range(n):
        if best[i] is None:
            continue
        segs, score, words, spans = best[i]  # type: ignore[misc]
        for j in range(i + 1, min(n, i + _MAX_SPAN) + 1):
            word = _lookup(idx, toks[i:j])
            if word is None:
                continue
            cand = (segs + 1, score - idx.freq[word], words + [word], spans + [" ".join(toks[i:j])])
            if best[j] is None or cand[:2] < best[j][:2]:  # type: ignore[index]
                best[j] = cand
    if best[n] is None:
        return None
    _, _, words, spans = best[n]  # type: ignore[misc]
    return words, spans
