"""kpi.check의 추가 조회.

KPI 미달이 보이면 설비 상태나 생산 실적을 더 보고 판정을 다시 만든다.
"""

from __future__ import annotations

import asyncio
import unittest

from src.application.graph.state import Dependencies
from src.application.subgraphs.base import SubgraphState
from src.application.subgraphs.kpi.check import KpiCheck, KpiCheckConfig
from src.domain.models import SnapshotContext
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


def run_kpi(max_rounds, by_kind):
    router = FakeRouter(by_kind)
    deps = Dependencies(data=router, llm=FakeLLMAdapter(model="fake-local", seed="t"))
    sub = KpiCheck(
        KpiCheckConfig(enabled=True, use_llm_judge=False, max_probe_rounds=max_rounds),
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
        """판정의 근거가 전부 실제 record id여야 한다."""
        out, _ = run_kpi(
            1, {"kpi": kpi_records(), "equipment_status": equipment_records(),
                "production": []}
        )
        known = {r.id for r in out["records"]} | {r.id for r in out["probe_records"]}
        for judgement in out["judgements"]:
            for ev in judgement.evidence:
                self.assertIn(ev, known)


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
