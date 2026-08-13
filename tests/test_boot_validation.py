"""부팅 검증 — config와 코드가 어긋나면 프로세스가 뜨지 않아야 한다.

여기서 안 잡히면 새벽 8시 배치에서 발견된다. 그리고 문제를 하나씩
보고하면 3단 merge 환경에서 고치는 사람이 지친다 — 한 번에 모아야 한다.
"""

import unittest

from src.config.registry import ConfigValidationError, validate_config
from tests.helpers import base_config, deep_copy


def subgraphs(patch=None) -> dict:
    subs = deep_copy(base_config()["subgraphs"])
    if patch:
        patch(subs)
    return subs


def problems_of(subs: dict) -> list[str]:
    try:
        validate_config(subs)
    except ConfigValidationError as exc:
        return exc.problems
    return []


class ValidConfigTest(unittest.TestCase):
    def test_실제_config는_검증을_통과한다(self):
        self.assertEqual(problems_of(subgraphs()), [])

    def test_통과하면_파싱된_모델을_돌려준다(self):
        parsed = validate_config(subgraphs())
        self.assertIn("kafka.lag", parsed)
        self.assertEqual(parsed["kafka.lag"].warn_lag, 5000)


class NameMismatchTest(unittest.TestCase):
    def test_config에_있는데_레지스트리에_없으면_실패(self):
        def patch(s):
            s["kafka.lgg"] = s.pop("kafka.lag")

        found = problems_of(subgraphs(patch))
        self.assertTrue(any("kafka.lgg" in p and "레지스트리에 없습니다" in p for p in found))

    def test_오타는_양방향으로_잡힌다(self):
        """없는 이름을 불렀고, 있는 이름이 안 불렸다 — 둘 다 보고한다."""

        def patch(s):
            s["kafka.lgg"] = s.pop("kafka.lag")

        found = problems_of(subgraphs(patch))
        self.assertTrue(any("kafka.lgg" in p for p in found))
        self.assertTrue(any("'kafka.lag'" in p and "고아" in p for p in found))

    def test_레지스트리에_있는데_config에_없으면_고아(self):
        def patch(s):
            s.pop("kpi.check")

        found = problems_of(subgraphs(patch))
        self.assertTrue(any("kpi.check" in p and "고아" in p for p in found))

    def test_슬롯_전용은_고아가_아니다(self):
        """material.stock_gumi는 @register(slot_only=True)라 config에 없어도 된다."""
        found = problems_of(subgraphs())
        self.assertFalse(any("stock_gumi" in p for p in found))

    def test_슬롯_대상으로_쓰이면_고아가_아니다(self):
        def patch(s):
            s.pop("kpi.check")
            s["kafka.lag"]["nodes"] = {"process": "kpi.check"}

        found = problems_of(subgraphs(patch))
        self.assertFalse(any("kpi.check" in p and "고아" in p for p in found))


class SchemaTest(unittest.TestCase):
    def test_타입이_틀리면_실패(self):
        def patch(s):
            s["kafka.lag"]["warn_lag"] = "다섯천"

        found = problems_of(subgraphs(patch))
        self.assertTrue(any("warn_lag" in p and "integer" in p for p in found))

    def test_키_오타는_조용히_무시되지_않는다(self):
        """extra='forbid'가 없으면 기본값으로 돌아 왜 안 먹는지 헤매게 된다."""

        def patch(s):
            s["kafka.lag"]["warn_lagg"] = 5000

        found = problems_of(subgraphs(patch))
        self.assertTrue(any("warn_lagg" in p and "Extra inputs" in p for p in found))

    def test_nodes와_llm은_스키마_검증에서_제외된다(self):
        """조립 지시라서 서브그래프 config 스키마가 아니다."""

        def patch(s):
            s["kafka.lag"]["nodes"] = {"output": "outputs.no_llm"}
            s["kafka.lag"]["llm"] = {"model": "other"}

        self.assertEqual(problems_of(subgraphs(patch)), [])


class SlotReferenceTest(unittest.TestCase):
    def test_없는_슬롯_대상은_실패(self):
        def patch(s):
            s["kafka.lag"]["nodes"] = {"process": "nope.missing"}

        found = problems_of(subgraphs(patch))
        self.assertTrue(any("nope.missing" in p for p in found))

    def test_공유_노드_부품은_유효한_대상(self):
        def patch(s):
            s["kafka.lag"]["nodes"] = {"output": "outputs.no_llm"}

        self.assertEqual(problems_of(subgraphs(patch)), [])

    def test_다른_서브그래프도_유효한_대상(self):
        """상속 클래스를 슬롯으로 쓰는 것이 GBM/FCT 분기의 핵심이다."""

        def patch(s):
            s["material.stock"]["nodes"] = {"process": "material.stock_gumi"}

        self.assertEqual(problems_of(subgraphs(patch)), [])


class AggregationTest(unittest.TestCase):
    def test_여러_문제를_한_번에_보고한다(self):
        def patch(s):
            s["kafka.lgg"] = s.pop("kafka.lag")       # 이름 오타 (양방향 2건)
            s["kpi.check"]["warn_gap_pct"] = "많이"    # 타입 오류
            s["alarm.trend"]["nodes"] = {"process": "없음"}  # 슬롯 참조

        found = problems_of(subgraphs(patch))
        self.assertGreaterEqual(len(found), 4)

    def test_예외_메시지에_전체_건수가_담긴다(self):
        def patch(s):
            s["kafka.lag"]["warn_lag"] = "x"

        try:
            validate_config(subgraphs(patch))
            self.fail("검증이 통과했습니다")
        except ConfigValidationError as exc:
            self.assertIn("설정 검증에 실패했습니다", str(exc))
            self.assertIn("warn_lag", str(exc))


if __name__ == "__main__":
    unittest.main()
