"""서브그래프 단위 테스트 — 가짜 포트를 주입해 process만 직접 호출한다.

여기 DB도 config 파일도 그래프도 없다. 포트가 Protocol이고 config가 조립
시점에만 쓰이는 설계라서 `await sub.process(state)` 한 줄로 검증된다.
새 분석을 만들 때 이 파일을 복사해 시작하면 된다.
"""

import asyncio
import unittest
from datetime import datetime

from src.application.subgraphs.base import SubgraphState
from src.application.subgraphs.kafka.lag import KafkaLag, KafkaLagConfig
from src.application.subgraphs.material.stock import MaterialStock, MaterialStockConfig
from src.application.subgraphs.material.stock_gumi import GumiMaterialStock
from src.domain.models import Record, Severity, SnapshotContext

CTX = SnapshotContext(as_of=datetime(2026, 8, 13, 8, 0), gbm="mx", factory="gumi")


class FakePort:
    """정해진 Record 목록을 돌려주는 포트. 이게 가짜 어댑터의 전부다."""

    def __init__(self, records: list[Record]) -> None:
        self.records = records
        self.calls: list = []

    async def fetch(self, ctx, spec):
        self.calls.append((ctx, spec))
        return self.records


class FakeDeps:
    def __init__(self, **ports):
        for name, port in ports.items():
            setattr(self, name, port)


def stock(line: str, material: str, qty: int, rate: int) -> Record:
    return Record(
        id=f"stock:{line}:{material}",
        metadata={"line": line, "material": material},
        record={"stock_qty": qty, "hourly_consumption": rate},
    )


def run(coro):
    return asyncio.run(coro)


class MaterialStockTest(unittest.TestCase):
    def _process(self, records, **overrides):
        cfg = MaterialStockConfig(enabled=True, **overrides)
        sub = MaterialStock(cfg, FakeDeps(redis=FakePort(records)))
        return run(sub.process(SubgraphState(ctx=CTX, scoped=CTX)))

    def test_소진_시간을_재고와_소비량으로_계산한다(self):
        out = self._process([stock("L1", "MAT-A", 1000, 100)])
        self.assertEqual(out["metrics"][0].value, 10.0)

    def test_임계치_아래면_경고한다(self):
        out = self._process([stock("L1", "MAT-A", 500, 100)], warn_hours=8.0)
        self.assertEqual(out["judgements"][0].severity, Severity.WARNING)

    def test_더_낮은_임계치는_심각으로_올라간다(self):
        out = self._process(
            [stock("L1", "MAT-A", 100, 100)], warn_hours=8.0, critical_hours=2.0
        )
        self.assertEqual(out["judgements"][0].severity, Severity.CRITICAL)

    def test_여유가_있으면_판정하지_않는다(self):
        out = self._process([stock("L1", "MAT-A", 5000, 100)], warn_hours=8.0)
        self.assertEqual(out["judgements"], [])
        self.assertTrue(out["metrics"], "판정이 없어도 지표는 남는다")

    def test_판정의_근거는_실제_record_id다(self):
        rec = stock("L2", "MAT-B", 100, 100)
        out = self._process([rec], warn_hours=8.0)
        self.assertEqual(out["judgements"][0].evidence, [rec.id])

    def test_소비량이_0이면_나눗셈에서_죽지_않는다(self):
        out = self._process([stock("L1", "MAT-A", 100, 0)])
        self.assertEqual(out["judgements"], [])

    def test_데이터가_없어도_빈_결과를_돌려준다(self):
        out = self._process([])
        self.assertEqual(out["metrics"], [])
        self.assertEqual(out["judgements"], [])


class GumiMaterialStockTest(unittest.TestCase):
    """상속 클래스는 같은 config로 다른 로직을 돈다."""

    def _process(self, records, as_of_hour: int, **overrides):
        ctx = SnapshotContext(
            as_of=datetime(2026, 8, 13, as_of_hour, 0), gbm="mx", factory="gumi"
        )
        cfg = MaterialStockConfig(enabled=True, **overrides)
        sub = GumiMaterialStock(cfg, FakeDeps(redis=FakePort(records)))
        return run(sub.process(SubgraphState(ctx=ctx, scoped=ctx)))

    def test_다음_입고_시각까지의_여유로_판정한다(self):
        # 8시 기준 다음 입고는 9시 → 1시간. 재고는 2시간치라 여유 +1h
        out = self._process([stock("L1", "MAT-A", 200, 100)], as_of_hour=8)
        self.assertIn("입고", out["metrics"][0].note)

    def test_입고_전에_소진되면_심각(self):
        # 10시 기준 다음 입고는 15시 → 5시간. 재고는 1시간치
        out = self._process([stock("L1", "MAT-A", 100, 100)], as_of_hour=10)
        self.assertEqual(out["judgements"][0].severity, Severity.CRITICAL)

    def test_마지막_입고_이후에는_다음날_첫_입고까지_본다(self):
        # 20시 → 다음 입고는 익일 9시 = 13시간 뒤
        self.assertEqual(GumiMaterialStock._hours_to_next_replenish(20), 13.0)

    def test_기본_클래스와_판정이_달라진다(self):
        """같은 데이터·같은 config인데 판정이 갈린다 — 이게 로직 override의 요점.

        주의: 두 클래스가 같은 config 키를 다른 의미로 쓴다. 기본은
        critical_hours를 "소진까지 남은 시간"으로, 구미는 "입고 후 여유"로
        해석한다.
        """
        records = [stock("L1", "MAT-A", 500, 100)]  # 5시간치
        base = MaterialStock(
            MaterialStockConfig(enabled=True, warn_hours=8.0),
            FakeDeps(redis=FakePort(records)),
        )
        base_out = run(base.process(SubgraphState(ctx=CTX, scoped=CTX)))
        # 8시 기준 다음 입고는 9시 → 여유 4시간
        gumi_out = self._process(records, as_of_hour=8, warn_hours=8.0)

        self.assertTrue(base_out["judgements"], "기본: 8시간 안에 소진되므로 경고")
        self.assertFalse(gumi_out["judgements"], "구미: 1시간 뒤 입고라 문제없음")


class KafkaLagTest(unittest.TestCase):
    def _lag(self, group: str, partition: int, lag: int) -> Record:
        return Record(
            id=f"lag:{group}:{partition}",
            metadata={"group": group, "topic": "t", "partition": partition},
            record={"end_offset": 1000, "committed_offset": 1000 - lag, "lag": lag},
        )

    def _process(self, records, **overrides):
        cfg = KafkaLagConfig(enabled=True, **overrides)
        sub = KafkaLag(cfg, FakeDeps(kafka=FakePort(records)))
        return run(sub.process(SubgraphState(ctx=CTX, scoped=CTX)))

    def test_그룹별로_lag을_합산한다(self):
        out = self._process([self._lag("g", 0, 100), self._lag("g", 1, 200)])
        self.assertEqual(out["metrics"][0].value, 300)

    def test_임계치를_넘으면_판정한다(self):
        out = self._process([self._lag("g", 0, 9999)], warn_lag=5000)
        self.assertTrue(any(j.severity == Severity.WARNING for j in out["judgements"]))

    def test_총량이_정상이어도_파티션_편중을_잡는다(self):
        """소비자 하나가 죽으면 총량은 괜찮은데 한 파티션만 밀린다."""
        records = [self._lag("g", 0, 900), self._lag("g", 1, 10), self._lag("g", 2, 10)]
        out = self._process(records, warn_lag=100_000, skew_ratio=2.0)
        self.assertTrue(any("편중" in j.subject for j in out["judgements"]))

    def test_고르게_분포하면_편중_판정이_없다(self):
        records = [self._lag("g", i, 100) for i in range(3)]
        out = self._process(records, warn_lag=100_000, skew_ratio=3.0)
        self.assertFalse(any("편중" in j.subject for j in out["judgements"]))

    def test_그룹_필터가_포트로_전달된다(self):
        port = FakePort([])
        sub = KafkaLag(KafkaLagConfig(enabled=True, groups=["a"]), FakeDeps(kafka=port))
        run(sub.process(SubgraphState(ctx=CTX, scoped=CTX)))
        self.assertEqual(port.calls[0][1].filters["groups"], ["a"])


if __name__ == "__main__":
    unittest.main()
