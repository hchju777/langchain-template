"""배경 문서 어댑터 — 파일에서 읽어 ReferenceDoc으로 바꾼다.

파일 읽기는 **조립 시점에** 끝낸다. 경로가 틀렸으면 부팅에서 멈춰야지,
새벽 배치가 돌다가 알게 되면 곤란하다. MarkdownRenderer가 템플릿에 대해
하는 것과 같은 방침이다.
"""

from __future__ import annotations

from pathlib import Path

from src.constants import PROJECT_ROOT
from src.domain.models import BaseContext, ReferenceDoc, Requirement


def _title_of(text: str, path: Path) -> str:
    """첫 번째 마크다운 제목. 없으면 파일 이름."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem


def _read(path: Path, doc_id: str) -> ReferenceDoc:
    text = path.read_text(encoding="utf-8")
    return ReferenceDoc(
        id=doc_id, source=str(path), title=_title_of(text, path), content=text
    )


class StaticReferenceAdapter:
    """config에 적힌 운영 문서와 실행 시 첨부한 문서를 함께 다룬다.

    상대 경로는 저장소 루트 기준으로 푼다. 사내 공유 드라이브처럼 바깥에
    있는 문서는 절대 경로로 적으면 그대로 통한다.
    """

    def __init__(
        self,
        config_paths: list[str] | None = None,
        attached_paths: list[str | Path] | None = None,
        root: Path | None = None,
    ) -> None:
        base = root or PROJECT_ROOT
        docs: list[ReferenceDoc] = []
        for index, raw in enumerate(config_paths or [], start=1):
            path = Path(raw)
            docs.append(_read(path if path.is_absolute() else base / path, f"sop-{index:02d}"))
        for index, raw in enumerate(attached_paths or [], start=1):
            path = Path(raw)
            docs.append(
                _read(path if path.is_absolute() else base / path, f"attach-{index:02d}")
            )
        self._docs = docs

    async def load(
        self, ctx: BaseContext, requirement: Requirement | None
    ) -> list[ReferenceDoc]:
        return list(self._docs)
