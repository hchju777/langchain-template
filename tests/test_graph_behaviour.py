"""그래프 동작 — 부분 실패 격리, 슬롯/모델 override, 멱등성.

전부 가짜 어댑터로 돌기 때문에 DB도 LLM도 필요 없다. 포트가 Protocol이고
config가 조립 시점에만 쓰이는 설계 덕분이다.
"""

import unittest

from src.config.loader import DeployConfig
from tests.helpers import (
    AS_OF,
    FACTORY,
    GBM,
    run_graph,
    section,
    temp_config,
    with_subgraph_patch,
)
from datetime import datetime


class PartialFailureTest(unittest.TestCase):
    """한 서브그래프의 실패가 리포트 전체를 죽이면 안 된다."""

    def _run_with_broken_alarm(self):
        def patch(cfg):
            cfg["subgraphs"]["alarm.trend"].pop("window")  # 구간 분석인데 window 없음

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            return run_graph(cfg)

    def test_실패한_서브그래프만_degraded된다(self):
        state = self._run_with_broken_alarm()
        self.assertTrue(section(state, "alarm.trend").degraded)

    def test_나머지_서브그래프는_정상_완료된다(self):
        state = self._run_with_broken_alarm()
        healthy = [s for s in state["sections"] if not s.degraded]
        self.assertGreaterEqual(len(healthy), 4)

    def test_실패해도_리포트는_생성된다(self):
        state = self._run_with_broken_alarm()
        self.assertTrue(state["rendered"])

    def test_실패_원인이_State에_기록된다(self):
        state = self._run_with_broken_alarm()
        errors = state["errors"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].key, "alarm.trend")
        self.assertEqual(errors[0].slot, "validate")
        self.assertIn("window", errors[0].message)

    def test_집계에_degraded_수가_반영된다(self):
        state = self._run_with_broken_alarm()
        self.assertEqual(state["overall"].counts["degraded"], 1)


class SubgraphToggleTest(unittest.TestCase):
    def test_끄면_섹션이_사라진다(self):
        def patch(cfg):
            cfg["subgraphs"]["kafka.lag"]["enabled"] = False

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            state = run_graph(cfg)
        keys = [s.key for s in state["sections"]]
        self.assertNotIn("kafka.lag", keys)
        self.assertIn("kpi.check", keys)

    def test_전부_끄면_조립_단계에서_막힌다(self):
        def patch(cfg):
            for name in cfg["subgraphs"]:
                cfg["subgraphs"][name]["enabled"] = False

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            with self.assertRaises(RuntimeError):
                run_graph(cfg)


class SlotOverrideTest(unittest.TestCase):
    def test_공유_부품으로_output을_교체하면_서술이_빠진다(self):
        def patch(cfg):
            cfg["subgraphs"]["kpi.check"]["nodes"] = {"output": "outputs.no_llm"}

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            state = run_graph(cfg)
        sec = section(state, "kpi.check")
        self.assertEqual(sec.narrative, "")
        self.assertTrue(sec.metrics, "지표는 그대로 남아야 한다")

    def test_no_llm이어도_관측_기록은_보존된다(self):
        """서술을 안 만든다고 프롬프트 원문과 가드레일 경고까지 잃으면 안 된다."""

        def patch(cfg):
            cfg["subgraphs"]["kpi.check"]["nodes"] = {"output": "outputs.no_llm"}

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            state = run_graph(cfg)
        self.assertTrue([t for t in state["traces"] if t.node == "kpi.check"])
        self.assertTrue(state["guardrail_drops"])

    def test_다른_서브그래프로_process를_교체할_수_있다(self):
        """상속은 코드에서, 선택은 config에서."""

        def patch(cfg):
            cfg["subgraphs"]["material.stock"]["nodes"] = {
                "process": "material.stock_gumi"
            }

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            state = run_graph(cfg)
        self.assertIn("입고", section(state, "material.stock").metrics[0].note)

    def test_override가_없으면_기본_로직이_돈다(self):
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            state = run_graph(cfg)
        note = section(state, "material.stock").metrics[0].note
        self.assertIn("시간당", note)
        self.assertNotIn("입고", note)

    def test_도너의_헬퍼_메서드에_접근할_수_있다(self):
        """메서드만 빌려오면 그 클래스의 헬퍼를 못 부른다 — 인스턴스를 만들어야 한다."""

        def patch(cfg):
            cfg["subgraphs"]["material.stock"]["nodes"] = {
                "process": "material.stock_gumi"
            }

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            state = run_graph(cfg)
        self.assertFalse(section(state, "material.stock").degraded)


class LLMOverrideTest(unittest.TestCase):
    def test_서브그래프별로_다른_모델을_쓸_수_있다(self):
        def patch(cfg):
            cfg["subgraphs"]["kafka.lag"]["llm"] = {"model": "special"}

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            state = run_graph(cfg)
        used = {t.node: t.model for t in state["traces"]}
        self.assertEqual(used["kafka.lag"], "special")
        self.assertNotEqual(used["line.equipment"], "special")

    def test_부분_override는_기본값을_상속한다(self):
        """model만 바꿔도 adapter는 기본 설정을 물려받는다."""

        def patch(cfg):
            cfg["llm"] = {"adapter": "fake", "model": "base"}
            cfg["subgraphs"]["kafka.lag"]["llm"] = {"model": "special"}

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            state = run_graph(cfg)
        self.assertEqual(
            [t.model for t in state["traces"] if t.node == "kafka.lag"][0], "special"
        )

    def test_취합은_항상_기본_LLM을_쓴다(self):
        def patch(cfg):
            cfg["llm"] = {"adapter": "fake", "model": "base"}
            cfg["subgraphs"]["kafka.lag"]["llm"] = {"model": "special"}

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            state = run_graph(cfg)
        used = {t.node: t.model for t in state["traces"]}
        self.assertEqual(used["aggregate"], "base")


class GuardrailTest(unittest.TestCase):
    """LLM이 없는 근거를 대면 그 판정은 폐기된다 — LLM 없이 코드로 검증한다."""

    def test_없는_근거를_댄_판정은_폐기된다(self):
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            state = run_graph(cfg)
        self.assertTrue(state["guardrail_drops"])
        self.assertIn("hallucinated", state["guardrail_drops"][0])

    def test_살아남은_판정의_근거는_실제_record_id다(self):
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            state = run_graph(cfg)
        for sec in state["sections"]:
            for j in sec.judgements:
                for ev in j.evidence:
                    self.assertNotIn("hallucinated", ev)


class IdempotencyTest(unittest.TestCase):
    """as_of가 결과를 결정한다. 노드가 datetime.now()를 부르면 여기가 깨진다."""

    def test_같은_as_of는_같은_리포트를_만든다(self):
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            first = run_graph(cfg)["rendered"]
            second = run_graph(cfg)["rendered"]
        self.assertEqual(first, second)

    def test_다른_as_of는_다른_데이터를_본다(self):
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            a = run_graph(cfg)["rendered"]
            b = run_graph(cfg, as_of=datetime(2026, 8, 12, 8, 0))["rendered"]
        self.assertNotEqual(a, b)

    def test_as_of가_모든_섹션에_일관되게_쓰인다(self):
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            state = run_graph(cfg)
        self.assertIn(AS_OF.isoformat(), state["rendered"])


class TimeWindowTest(unittest.TestCase):
    def test_구간_분석은_window로_절대_시각을_만든다(self):
        """config의 "P7D"가 as_of 기준 절대 시각 쌍으로 변환된다."""
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            state = run_graph(cfg)
        # 첫 지표가 집계 구간을 보여준다: "08-06 ~ 08-13" (as_of 기준 7일 전 ~ as_of)
        span = section(state, "alarm.trend").metrics[0].value
        self.assertEqual(span, "08-06 ~ 08-13")

    def test_window를_바꾸면_구간이_따라_바뀐다(self):
        def patch(cfg):
            cfg["subgraphs"]["alarm.trend"]["window"] = "P2D"

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            state = run_graph(cfg)
        self.assertEqual(section(state, "alarm.trend").metrics[0].value, "08-11 ~ 08-13")

    def test_스냅샷_분석은_window를_무시한다(self):
        def patch(cfg):
            cfg["subgraphs"]["kafka.lag"]["window"] = "PT24H"

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            state = run_graph(cfg)
        self.assertFalse(section(state, "kafka.lag").degraded)


class DeliveryTest(unittest.TestCase):
    def test_채널을_전부_끄면_아무것도_발송되지_않는다(self):
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            state = run_graph(cfg)
        self.assertEqual(state["delivered"], [])
        self.assertTrue(state["rendered"], "발송과 무관하게 리포트는 만들어진다")


if __name__ == "__main__":
    unittest.main()
