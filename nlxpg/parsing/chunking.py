"""구조 기반 섹션 분할.

장문 문서(Doc2DB-Bench 평균 약 43K 토큰)는 한 번에 넣지 않고 섹션 단위로 추출한 뒤
정합 단계에서 병합한다(plan.md §리스크). 마크다운 제목을 경계로 나누고, 상한을 넘는
섹션만 문단 경계에서 다시 자른다. 섹션 제목 경로는 evidence의 span으로 쓰인다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


@dataclass
class Section:
    doc_id: str
    index: int
    path: str  # "제2장 > 제3조" 형태의 제목 경로
    text: str


def split_sections(doc_id: str, markdown: str, *, max_chars: int = 12_000) -> list[Section]:
    raw: list[tuple[str, list[str]]] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    path = ""

    for line in markdown.splitlines():
        m = _HEADING.match(line)
        if m:
            if "".join(buf).strip():
                raw.append((path, buf))
            level, title = len(m.group(1)), m.group(2)
            stack = [(lv, t) for lv, t in stack if lv < level] + [(level, title)]
            path = " > ".join(t for _, t in stack)
            buf = [line]
        else:
            buf.append(line)
    if "".join(buf).strip():
        raw.append((path, buf))

    sections: list[Section] = []
    for sec_path, lines in raw:
        for piece in _split_long("\n".join(lines).strip(), max_chars):
            sections.append(Section(doc_id, len(sections), sec_path or "(서두)", piece))
    return _merge_small(sections, max_chars)


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    cur = ""
    for para in re.split(r"\n\s*\n", text):
        if cur and len(cur) + len(para) + 2 > max_chars:
            pieces.append(cur)
            cur = ""
        # 문단 하나가 상한을 넘으면 어쩔 수 없이 글자 수로 자른다.
        while len(para) > max_chars:
            pieces.append(para[:max_chars])
            para = para[max_chars:]
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        pieces.append(cur)
    return pieces


def _merge_small(sections: list[Section], max_chars: int) -> list[Section]:
    """짧은 섹션이 잘게 쪼개지면 LLM 호출 수만 늘고 문맥이 끊긴다. 상한 안에서 이웃과 합친다."""
    merged: list[Section] = []
    for s in sections:
        if merged and len(merged[-1].text) + len(s.text) + 2 <= max_chars:
            prev = merged[-1]
            first = prev.path.split(" ~ ")[0]
            path = first if first == s.path else f"{first} ~ {s.path}"
            merged[-1] = Section(prev.doc_id, prev.index, path, f"{prev.text}\n\n{s.text}")
        else:
            merged.append(Section(s.doc_id, len(merged), s.path, s.text))
    return merged
