"""배경 문서 공급. 파일 읽기는 조립 시점에 끝나야 한다."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from src.domain.models import BaseContext
from src.infrastructure.references import StaticReferenceAdapter
from tests.helpers import AS_OF

CTX = BaseContext(as_of=AS_OF, gbm="mx", factory="gumi")


def _write(root: Path, name: str, body: str) -> str:
    (root / name).write_text(body, encoding="utf-8")
    return name


class StaticReferenceAdapterTest(unittest.TestCase):
    """어댑터를 직접 테스트하는 예외다. 외부 의존이 없는 파일 읽기이고,
    '경로가 틀리면 부팅에서 멈춘다'가 이 어댑터의 핵심 동작이라 검증 가치가 크다.
    """

    def test_config_documents_get_sop_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _write(root, "sop.md", "# 정기점검 기준\n본문")
            b = _write(root, "kpi.md", "# KPI 정의\n본문")
            docs = asyncio.run(
                StaticReferenceAdapter([a, b], root=root).load(CTX, None)
            )
        self.assertEqual([d.id for d in docs], ["sop-01", "sop-02"])
        self.assertEqual(docs[0].title, "정기점검 기준")
        self.assertIn("본문", docs[0].content)

    def test_attached_documents_get_attach_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _write(root, "sop.md", "# 기준\n본문")
            b = _write(root, "plan.md", "# 정비 계획\n오늘 A라인 점검")
            docs = asyncio.run(
                StaticReferenceAdapter(
                    [a], attached_paths=[root / b], root=root
                ).load(CTX, None)
            )
        self.assertEqual([d.id for d in docs], ["sop-01", "attach-01"])
        self.assertEqual(docs[1].title, "정비 계획")

    def test_missing_file_fails_at_construction(self):
        """새벽 실행 중에 발견되는 것보다 부팅에서 멈추는 게 낫다."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError) as caught:
                StaticReferenceAdapter(["없는파일.md"], root=Path(tmp))
        self.assertIn("없는파일.md", str(caught.exception))

    def test_no_documents_is_fine(self):
        docs = asyncio.run(StaticReferenceAdapter([]).load(CTX, None))
        self.assertEqual(docs, [])

    def test_title_falls_back_to_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = _write(root, "note.md", "제목 없는 본문")
            docs = asyncio.run(StaticReferenceAdapter([a], root=root).load(CTX, None))
        self.assertEqual(docs[0].title, "note")
