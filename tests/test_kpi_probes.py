"""kpi.check의 추가 조회.

KPI 미달이 보이면 설비 상태나 생산 실적을 더 보고 판정을 다시 만든다.
"""

from __future__ import annotations

import asyncio
import unittest

from src.application.graph.state import Dependencies
from src.application.subgraphs.base import SubgraphState
from src.application.subgraphs.kpi.check import KpiCheck, KpiCheckConfig
from src.domain.models import Judgement, Severity, SnapshotContext
from src.infrastructure.llm import FakeLLMAdapter
from tests.helpers import AS_OF, base_config, temp_config, with_subgraph_patch

CTX = SnapshotContext(as_of=AS_OF, gbm="mx", factory="gumi")


class FakeRouter:
    """kind별로 정해진 record를 돌려준다. 어느 kind가 요청됐는지 기록한다."""

    def __init__(self, by_kind):
        self.by_kind = by_kind
        self.asked = []

    async def fetch(self, ctx, spec):
        self.asked.append(spec.kind)
        return list(self.by_kind.get(spec.kind, []))

    def routed_kinds(self):
        return set(self.by_kind)


def run_kpi(max_rounds, by_kind, *, use_llm_judge=False, llm=None):
    router = FakeRouter(by_kind)
    deps = Dependencies(
        data=router, llm=llm or FakeLLMAdapter(model="fake-local", seed="t")
    )
    sub = KpiCheck(
        KpiCheckConfig(
            enabled=True, use_llm_judge=use_llm_judge, max_probe_rounds=max_rounds
        ),
        deps,
    )
    out = asyncio.run(sub.compile().ainvoke(SubgraphState(ctx=CTX, scoped=CTX)))
    return out, router


def kpi_records():
    from src.domain.models import Record

    return [Record(id="kpi:수율", metadata={"kpi": "수율", "unit": "%"},
                   record={"value": 88.0, "target": 96.0, "gap_pct": -8.3})]


def equipment_records():
    from src.domain.models import Record

    return [Record(id="eq:L1:EQ-3", metadata={"line": "L1", "equipment_id": "EQ-3"},
                   record={"state": "DOWN"})]


class KpiProbeTest(unittest.TestCase):
    def test_declares_the_kinds_its_probes_open(self):
        """선언하지 않은 데이터는 열 수 없다."""
        for kind in ("kpi", "equipment_status", "production"):
            self.assertIn(kind, KpiCheck.required_kinds)

    def test_probe_kinds_cannot_drift_from_the_kind_it_fetches(self):
        """조립이 보는 kinds와 fetch가 쓰는 kind는 같은 출처여야 한다.

        따로 적혀 있으면 어긋나도 조립을 통과하고, 실행에서 매번 터진다.
        """
        from src.application.subgraphs.kpi.check import EquipmentProbe, ProductionProbe

        for probe in (EquipmentProbe(), ProductionProbe()):
            self.assertEqual(probe.kinds, (probe.kind,))

    def test_no_probe_when_rounds_are_zero(self):
        out, router = run_kpi(0, {"kpi": kpi_records()})
        self.assertEqual(router.asked, ["kpi"])
        self.assertEqual(out["probe_records"], [])

    def test_probe_fetches_a_declared_kind(self):
        out, router = run_kpi(
            1, {"kpi": kpi_records(), "equipment_status": equipment_records(),
                "production": []}
        )
        self.assertGreater(len(router.asked), 1)
        for kind in router.asked:
            self.assertIn(kind, KpiCheck.required_kinds)

    def test_metrics_come_from_base_records_only(self):
        """지표는 KPI에서만 나온다. probe가 지표를 늘리면 안 된다."""
        without, _ = run_kpi(0, {"kpi": kpi_records()})
        with_probe, _ = run_kpi(
            1, {"kpi": kpi_records(), "equipment_status": equipment_records(),
                "production": []}
        )
        self.assertEqual([m.name for m in without["metrics"]],
                         [m.name for m in with_probe["metrics"]])

    def test_threshold_judgement_survives_the_rounds(self):
        """judge가 판정을 통째로 교체해도 임계치 판정은 매번 다시 만들어진다."""
        out, _ = run_kpi(
            1, {"kpi": kpi_records(), "equipment_status": equipment_records(),
                "production": []}
        )
        subjects = [j.subject for j in out["judgements"]]
        self.assertTrue(any("수율" in s for s in subjects), subjects)

    def test_all_evidence_is_traceable(self):
        """판정의 근거가 전부 실제 record id여야 한다.

        LLM judge를 켜야 의미가 있다. 꺼두면 근거를 코드가 직접 넣은 판정만
        남아 단언이 무조건 참이 된다 — 기본 어댑터는 가드레일 시연용으로
        일부러 없는 id를 하나 섞으므로, 켜야 실제로 걸러지는지 확인된다.
        """
        out, _ = run_kpi(
            1, {"kpi": kpi_records(), "equipment_status": equipment_records(),
                "production": []},
            use_llm_judge=True,
        )
        known = {r.id for r in out["records"]} | {r.id for r in out["probe_records"]}
        self.assertTrue(out["judgements"])
        for judgement in out["judgements"]:
            for ev in judgement.evidence:
                self.assertIn(ev, known)


class ProbeCitingLLM(FakeLLMAdapter):
    """probe가 가져온 record만 근거로 드는 판정을 만든다.

    라운드 0에서는 그 id가 아직 없으므로 가드레일이 이 판정을 버린다.
    probe가 돌고 난 라운드에서는 살아남아야 한다.
    """

    PROBE_ID = "eq:L1:EQ-3"

    async def _judge_raw(self, prompt, allowed_ids):
        return [
            Judgement(
                subject="설비 정지가 수율 미달과 겹칩니다",
                severity=Severity.WARNING,
                reasoning="정지 설비가 있는 라인에서 수율이 함께 떨어졌습니다",
                evidence=[self.PROBE_ID],
                confidence=0.8,
            )
        ]


class ProbeEvidenceGuardrailTest(unittest.TestCase):
    """이 브랜치의 핵심 주장: probe가 가져온 데이터를 인용한 판정이 근거
    가드레일에 폐기되지 않는다.

    judge가 allowed_ids로 records만 넘기면 probe 데이터를 인용한 판정이
    통째로 사라진다. 그때는 리포트가 조용히 얌전해질 뿐이라 아무도 모른다.
    그래서 llm.judge()를 실제로 지나는 경로로 확인한다.
    """

    def run_with_citing_judge(self):
        return run_kpi(
            1,
            {"kpi": kpi_records(), "equipment_status": equipment_records(),
             "production": []},
            use_llm_judge=True,
            llm=ProbeCitingLLM(model="fake-local", seed="t"),
        )

    def test_probe_evidence_survives_the_guardrail(self):
        out, _ = self.run_with_citing_judge()
        cited = [j for j in out["judgements"]
                 if ProbeCitingLLM.PROBE_ID in j.evidence]
        self.assertTrue(
            cited, "probe가 가져온 근거를 든 판정이 남아야 한다"
        )

    def test_the_probe_record_was_actually_fetched(self):
        """단언이 우연히 참이 되지 않도록 전제를 함께 고정한다."""
        out, _ = self.run_with_citing_judge()
        self.assertIn(ProbeCitingLLM.PROBE_ID,
                      [r.id for r in out["probe_records"]])
        self.assertNotIn(ProbeCitingLLM.PROBE_ID,
                         [r.id for r in out["records"]])


class JudgePromptTest(unittest.TestCase):
    """judge 프롬프트의 사실 목록은 리포트에 그대로 인쇄된다.

    가짜 어댑터가 '- '로 시작하는 줄을 판정 근거 문장으로 옮기고, 렌더러가
    그 문장을 찍는다. 프롬프트를 손대는 사람이 이 연결을 모르면 dict repr이
    다시 사용자 눈앞까지 흘러간다 — 그래서 표현 자체를 여기서 고정한다.
    """

    def prompt(self):
        sub = KpiCheck(KpiCheckConfig(enabled=True), Dependencies())
        return sub._judge_prompt(kpi_records(), equipment_records())

    def test_no_dict_repr_reaches_the_prompt(self):
        self.assertNotIn("{'", self.prompt())

    def test_kpi_fact_is_readable(self):
        line = next(ln for ln in self.prompt().splitlines() if "kpi:수율" in ln)
        self.assertIn("수율", line)
        self.assertIn("88.0%", line)
        self.assertIn("목표 대비 -8.3%", line)

    def test_probe_fact_is_readable(self):
        line = next(ln for ln in self.prompt().splitlines() if "eq:L1:EQ-3" in ln)
        self.assertIn("EQ-3", line)
        self.assertIn("DOWN", line)

    def test_every_id_stays_in_brackets(self):
        """가드레일의 인용 어휘는 프롬프트 표현이 바뀌어도 그대로여야 한다."""
        prompt = self.prompt()
        for rec in kpi_records() + equipment_records():
            self.assertIn(f"[{rec.id}]", prompt)


class KpiConfigTest(unittest.TestCase):
    def test_shipped_config_enables_probing(self):
        cfg = base_config()
        self.assertEqual(cfg["subgraphs"]["kpi.check"]["max_probe_rounds"], 2)

    def test_boot_validation_accepts_the_wider_kinds(self):
        """probe가 여는 kind가 ports에 매핑돼 있어야 부팅이 통과한다."""
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as deploy:
            ports = deploy.section("ports")
        for kind in KpiCheck.required_kinds:
            self.assertIn(kind, ports)
