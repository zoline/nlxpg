"""설계 실행 1회: 문서 기록 → 실행 이력 시작 → 파이프라인 → 결과 기록.

CLI(`nlxpg design`)와 웹 API(`POST /api/runs`)가 같은 경로를 쓴다. API는 start()로
run_id를 먼저 돌려준 뒤 execute()를 백그라운드로 돌린다.
"""
from __future__ import annotations

import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nlxpg.extraction.extractor import ProgressFn
from nlxpg.llm import create_llm
from nlxpg.llm.factory import guided_mode
from nlxpg.parsing import Document
from nlxpg.pipeline import DesignResult, design_from_documents
from nlxpg.settings import Settings
from nlxpg.standards import Standards
from nlxpg.store import RunStore


def validation_dict(result: DesignResult) -> dict[str, Any]:
    return {
        "lint": [asdict(i) for i in result.lint],
        "sandbox": asdict(result.sandbox) if result.sandbox else None,
        "normal_forms": [asdict(f) for f in result.normal_forms],
    }


class RunService:
    def __init__(
        self, settings: Settings, standards: Standards | None, store: RunStore | None,
        llm_settings: Settings | None = None,
    ):
        """llm_settings: DB 프로필로 만든 추출용 LLM 설정(llm/profiles.py). LLM은 DB에서만 읽는다."""
        self.settings = settings
        self.llm_settings = llm_settings or settings
        self.standards = standards
        self.store = store

    async def start(
        self, docs: list[Document], *, label: str | None, internal: bool,
        extra_config: dict[str, Any] | None = None, created_by: int | None = None,
    ) -> int | None:
        """실행 이력을 만든다. 시스템 DB가 없으면 None.

        문서 ID에 내용 해시를 붙인다(`게시판-1a2b3c4d`). 파일 이름만 쓰면 같은 이름의 다른
        문서를 올릴 때 이전 실행의 원문을 덮어쓴다(2026-10-01 발견). 같은 내용이면 같은 ID라
        문서 행을 공유하고, 실행을 지울 때는 아무 실행도 쓰지 않는 문서만 지운다.
        """
        if self.store is None:
            return None
        s = self.settings
        for d in docs:
            d.doc_id = versioned_doc_id(d)
        for d in docs:
            await self.store.upsert_document(d, classification="internal" if internal else "public")
        return await self.store.start_run(
            [d.doc_id for d in docs], label=label,
            provider=self.llm_settings.llm_provider, model=self.llm_settings.llm_model,
            config={
                "chunk_max_chars": s.chunk_max_chars,
                "guided_mode": guided_mode(self.llm_settings),
                "standards_version": self.standards.version if self.standards else None,
                **(extra_config or {}),
            },
            created_by=created_by,
        )

    async def execute(
        self, run_id: int | None, docs: list[Document], *, sandbox: bool,
        progress: ProgressFn | None = None, debug: bool = False,
    ) -> DesignResult:
        """debug: LLM 호출마다 프롬프트·응답·토큰·시간·오류를 nlxpg_llm_calls에 남긴다."""
        s = self.settings
        llm = create_llm(self.llm_settings)
        if debug and self.store and run_id is not None:
            store, model = self.store, llm.model

            async def record(rec: dict[str, Any]) -> None:
                await store.add_llm_call(run_id, model, rec)

            llm.recorder = record
        try:
            result = await design_from_documents(
                llm, docs, chunk_max_chars=s.chunk_max_chars, concurrency=s.extract_concurrency,
                sandbox_dsn=s.sandbox_pg_dsn if sandbox else None, standards=self.standards,
                progress=progress,
            )
        except Exception as exc:
            if self.store and run_id is not None:
                await self.store.finish_run(run_id, ir=None, ddl=None, ddl_valid=None,
                                            validation=None, error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            await llm.aclose()

        if self.store and run_id is not None:
            await self.store.finish_run(run_id, ir=result.schema, ddl=result.ddl,
                                        ddl_valid=result.valid, validation=validation_dict(result))
        return result


def versioned_doc_id(doc: Document) -> str:
    suffix = doc.sha256[:8]
    return doc.doc_id if doc.doc_id.endswith(f"-{suffix}") else f"{doc.doc_id}-{suffix}"


def _inside(path: Path, root: Path) -> bool:
    """path가 root 아래인가. 업로드 폴더 밖의 파일은 절대 지우지 않기 위한 확인."""
    try:
        return path.resolve().is_relative_to(root.resolve()) and path.resolve() != root.resolve()
    except OSError:
        return False


async def delete_run(store: RunStore, settings: Settings, run_id: int) -> dict[str, Any] | None:
    """실행 이력 삭제 + 이 실행만 쓰던 문서와 업로드 파일 정리. 없는 run_id면 None.

    파일은 settings.upload_dir 아래에 있을 때만 지운다(CLI로 실행한 문서는 사용자 파일이라
    건드리지 않는다). 업로드 폴더는 남은 문서가 그 안의 파일을 가리키지 않을 때만 지운다.
    """
    result = await store.delete_run(run_id)
    if result is None:
        return None
    root = settings.upload_dir
    removed: list[str] = []
    for d in result["deleted_documents"]:
        path = Path(d["source_path"] or "")
        if d["source_path"] and _inside(path, root) and path.is_file():
            path.unlink()
            removed.append(str(path.resolve().relative_to(root.resolve())))
    folder_name = (result["config"] or {}).get("upload_dir")
    if folder_name:
        folder = root / folder_name
        if _inside(folder, root) and folder.is_dir() and not await store.documents_under(str(folder)):
            shutil.rmtree(folder)
            removed.append(f"{folder_name}/")
    result["removed_files"] = removed
    del result["config"]
    return result
