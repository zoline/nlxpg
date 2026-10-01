"""문서 → 검증된 스키마. plan.md §시스템 아키텍처의 2~7단계.

검증 실패 시 모델링 단계로 돌아가는 수정 루프는 아직 없다 — 지금 모델링은 결정론적이라
다시 돌려도 결과가 같다. LLM 기반 수정 단계를 붙일 때 여기에 루프를 추가한다.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from nlxpg.extraction import extract_document
from nlxpg.extraction.extractor import ProgressFn
from nlxpg.generate import to_ddl, to_markdown, to_mermaid
from nlxpg.ir import Entity, SchemaIR
from nlxpg.llm.base import StructuredLLM
from nlxpg.modeling import design_schema
from nlxpg.parsing import Document
from nlxpg.reconcile import merge_irs, reconcile
from nlxpg.reconcile.merge import SameEntityFn, same_by_name
from nlxpg.standards import Standards, resolve_entity
from nlxpg.validate import LintIssue, SandboxResult, lint_schema, run_in_sandbox
from nlxpg.validate.normalform import NFFinding, check_normal_forms

log = logging.getLogger(__name__)


@dataclass
class DesignResult:
    extracted: SchemaIR  # 정합 전 (디버깅·평가용)
    schema: SchemaIR
    ddl: str
    erd: str
    description: str
    lint: list[LintIssue] = field(default_factory=list)
    sandbox: SandboxResult | None = None
    #: 정규형 검사. 위반이 있어도 DDL 유효성(valid)에는 넣지 않는다 — 의도적 중복일 수 있다.
    normal_forms: list[NFFinding] = field(default_factory=list)

    @property
    def valid(self) -> bool | None:
        if any(i.level == "error" for i in self.lint):
            return False
        if self.sandbox is None:
            return None  # 샌드박스를 안 돌렸으면 판정 보류
        return self.sandbox.ok


async def design_from_documents(
    llm: StructuredLLM,
    docs: list[Document],
    *,
    chunk_max_chars: int = 12_000,
    concurrency: int = 4,
    sandbox_dsn: str | None = None,
    standards: Standards | None = None,
    progress: ProgressFn | None = None,
) -> DesignResult:
    def emit(event: dict) -> None:
        if progress:
            progress(event)

    partials = await asyncio.gather(*(
        extract_document(llm, d, max_chars=chunk_max_chars, concurrency=concurrency, progress=progress)
        for d in docs
    ))
    extracted = merge_irs(list(partials))
    log.info("추출: 엔티티 %d, 관계 %d", len(extracted.entities), len(extracted.relationships))

    emit({"stage": "design"})
    same_entity = _same_by_standard(standards) if standards is not None else same_by_name
    schema = design_schema(reconcile(extracted, same_entity=same_entity), standards=standards)
    log.info("설계: 테이블 %d, 관계 %d", len(schema.entities), len(schema.relationships))

    ddl = to_ddl(schema)
    result = DesignResult(
        extracted=extracted, schema=schema, ddl=ddl,
        erd=to_mermaid(schema), description=to_markdown(schema), lint=lint_schema(schema),
        normal_forms=check_normal_forms(schema, standards),
    )
    if sandbox_dsn:
        emit({"stage": "sandbox"})
        result.sandbox = await run_in_sandbox(sandbox_dsn, ddl)
        log.info("샌드박스: %s", "통과" if result.sandbox.ok else result.sandbox.error)
    return result


def _same_by_standard(std: Standards) -> SameEntityFn:
    """이름이 달라도 표준단어로 바꾸면 같은 테이블명이 되는 엔터티(이음동의어 등)를 합친다."""

    def same(a: Entity, b: Entity) -> bool:
        if same_by_name(a, b):
            return True
        pa = resolve_entity(std, a.logical_name).physical_name
        return pa is not None and pa == resolve_entity(std, b.logical_name).physical_name

    return same
