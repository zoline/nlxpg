"""섹션 → ExtractionResult → 문서 단위 부분 IR."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from nlxpg.extraction.prompts import SYSTEM, USER_TEMPLATE
from nlxpg.extraction.schema import ExtractionResult
from nlxpg.ir import Attribute, Entity, Evidence, Relationship, SchemaIR, SourceDocument
from nlxpg.llm.base import StructuredLLM, call_label
from nlxpg.parsing import Document, Section, split_sections

log = logging.getLogger(__name__)

#: 진행 이벤트 수신자. 이벤트는 {"stage": ..., ...} 형태의 dict다(pipeline.py 참고).
ProgressFn = Callable[[dict[str, Any]], None]


async def extract_section(llm: StructuredLLM, doc: Document, section: Section) -> ExtractionResult:
    user = USER_TEMPLATE.format(title=doc.title, section_path=section.path, text=section.text)
    token = call_label.set(f"{doc.title} › {section.path}")
    try:
        return await llm.complete_json(SYSTEM, user, ExtractionResult)
    finally:
        call_label.reset(token)


async def extract_document(
    llm: StructuredLLM, doc: Document, *, max_chars: int = 12_000, concurrency: int = 4,
    progress: ProgressFn | None = None,
) -> SchemaIR:
    """섹션별 추출 결과를 이어 붙인 부분 IR을 돌려준다. 중복 통합은 정합 단계가 맡는다."""
    sections = split_sections(doc.doc_id, doc.text, max_chars=max_chars)
    sem = asyncio.Semaphore(concurrency)
    done = 0
    if progress:
        progress({"stage": "extract", "doc_id": doc.doc_id, "done": 0, "total": len(sections)})

    async def run(sec: Section) -> tuple[Section, ExtractionResult]:
        nonlocal done
        async with sem:
            log.info("추출 %s [%d/%d] %s", doc.doc_id, sec.index + 1, len(sections), sec.path)
            result = await extract_section(llm, doc, sec)
        done += 1
        if progress:
            progress({"stage": "extract", "doc_id": doc.doc_id, "done": done,
                      "total": len(sections), "section": sec.path})
        return sec, result

    results = await asyncio.gather(*(run(s) for s in sections))

    ir = SchemaIR(source_documents=[SourceDocument(doc_id=doc.doc_id, title=doc.title)])
    for sec, res in results:
        _append(ir, doc.doc_id, sec, res)
    return ir


def _evidence(doc_id: str, sec: Section, note: str) -> list[Evidence]:
    span = f"{sec.path} | {note}" if note else sec.path
    return [Evidence(doc_id=doc_id, span=span)]


def _append(ir: SchemaIR, doc_id: str, sec: Section, res: ExtractionResult) -> None:
    for e in res.entities:
        ir.entities.append(
            Entity(
                logical_name=e.logical_name,
                physical_name=e.physical_name,
                description=e.description,
                aliases=e.aliases,
                evidence=_evidence(doc_id, sec, e.evidence),
                attributes=[
                    Attribute(
                        logical_name=a.logical_name,
                        physical_name=a.physical_name,
                        description=a.description,
                        data_type=a.data_type_hint or "text",
                        nullable=not a.required,
                        is_unique=a.identifier,
                        value_examples=a.value_examples,
                        evidence=_evidence(doc_id, sec, a.evidence),
                    )
                    for a in e.attributes
                ],
            )
        )
    for r in res.relationships:
        ir.relationships.append(
            Relationship(
                name=r.name,
                from_entity=r.from_entity,
                to_entity=r.to_entity,
                cardinality=r.cardinality,
                description=r.description,
                evidence=_evidence(doc_id, sec, r.evidence),
            )
        )
