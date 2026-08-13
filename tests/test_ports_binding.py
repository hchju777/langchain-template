"""ports 바인딩 — "무엇을 어디서 가져올지"를 config가 정한다.

서브그래프는 kind만 말하고 저장소를 모른다. 그래서 같은 분석 코드가
공장마다 다른 저장소를 보게 만들 수 있다.
"""

import unittest

from src.application.graph.builder import build_dependencies
from src.config.env import EnvConfig
from src.config.registry import ConfigValidationError, validate_config
from src.domain.models import FetchSpec
from src.infrastructure.router import DataRouter, UnroutedKindError
from tests.helpers import (
    base_config,
    deep_copy,
    run_graph,
    section,
    temp_config,
    with_subgraph_patch,
)


class FakeAdapter:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.calls: list[str] = []

    async def fetch(self, ctx, spec):
        self.calls.append(spec.kind)
        return []


class DataRouterTest(unittest.TestCase):
    def test_kind로_어댑터를_고른다(self):
        a, b = FakeAdapter("a"), FakeAdapter("b")
        router = DataRouter({"x": a, "y": b})
        self.assertIs(router.adapter_for("x"), a)
        self.assertIs(router.adapter_for("y"), b)

    def test_매핑에_없는_kind는_실패한다(self):
        router = DataRouter({"x": FakeAdapter("a")})
        with self.assertRaises(UnroutedKindError) as ctx:
            router.adapter_for("없음")
        self.assertIn("없음", str(ctx.exception))
        self.assertIn("x", str(ctx.exception), "쓸 수 있는 kind를 알려줘야 한다")

    def test_라우팅된_kind_목록을_알려준다(self):
        router = DataRouter({"x": FakeAdapter("a"), "y": FakeAdapter("b")})
        self.assertEqual(router.routed_kinds(), {"x", "y"})


class PortsConfigTest(unittest.TestCase):
    def test_실제_config의_ports가_전부_유효한_어댑터를_가리킨다(self):
        with temp_config(gbm=base_config()) as cfg:
            deps = build_dependencies(cfg, EnvConfig())
        self.assertTrue(deps.data.routed_kinds())

    def test_없는_어댑터를_가리키면_조립에서_실패한다(self):
        cfg_data = deep_copy(base_config())
        cfg_data["ports"]["kpi"] = "없는어댑터"
        with temp_config(gbm=cfg_data) as cfg:
            with self.assertRaises(ValueError) as ctx:
                build_dependencies(cfg, EnvConfig())
        self.assertIn("없는어댑터", str(ctx.exception))

    def test_저장소를_바꿔도_서브그래프_코드는_그대로다(self):
        """material_stock을 Redis 대신 REST에서 읽어도 분석이 그대로 돈다."""

        def patch(cfg):
            cfg["ports"]["material_stock"] = "rest"

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            deps = build_dependencies(cfg, EnvConfig())
            self.assertEqual(deps.data.adapter_for("material_stock").name, "rest")
            state = run_graph(cfg)
        self.assertFalse(section(state, "material.stock").degraded)
        self.assertTrue(section(state, "material.stock").metrics)

    def test_지원하지_않는_kind를_보내면_부팅에서_막힌다(self):
        """저장소 교체는 그쪽 어댑터가 그 kind를 다룰 줄 알 때만 가능하다.

        여기서 안 막으면 실행 중에야 알게 된다.
        """

        def patch(cfg):
            cfg["ports"]["material_stock"] = "mongodb"

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            with self.assertRaises(ValueError) as ctx:
                build_dependencies(cfg, EnvConfig())
        message = str(ctx.exception)
        self.assertIn("mongodb", message)
        self.assertIn("material_stock", message)
        self.assertIn("alarms", message, "지원하는 kind를 알려줘야 한다")

    def test_같은_어댑터를_여러_kind가_공유한다(self):
        with temp_config(gbm=base_config()) as cfg:
            deps = build_dependencies(cfg, EnvConfig())
        self.assertIs(
            deps.data.adapter_for("production"), deps.data.adapter_for("line_info")
        )


class RequiredKindsValidationTest(unittest.TestCase):
    """서브그래프가 쓰는 kind가 ports에 없으면 부팅 때 잡아야 한다."""

    def _validate(self, patch, routed):
        subs = deep_copy(base_config()["subgraphs"])
        patch(subs)
        try:
            validate_config(subs, routed_kinds=routed)
        except ConfigValidationError as exc:
            return exc.problems
        return []

    def test_매핑이_없으면_실패한다(self):
        all_kinds = set(base_config()["ports"])
        found = self._validate(lambda s: None, all_kinds - {"kpi"})
        self.assertTrue(any("kpi" in p and "kpi.check" in p for p in found))

    def test_전부_매핑되면_통과한다(self):
        self.assertEqual(self._validate(lambda s: None, set(base_config()["ports"])), [])

    def test_꺼진_서브그래프의_kind는_없어도_된다(self):
        def patch(s):
            s["kpi.check"]["enabled"] = False

        all_kinds = set(base_config()["ports"])
        found = self._validate(patch, all_kinds - {"kpi"})
        self.assertEqual(found, [])

    def test_슬롯_override_대상의_kind도_확인한다(self):
        """다른 서브그래프가 대신 돌면 그쪽이 요청하는 kind도 필요하다."""

        def patch(s):
            s["kafka.lag"]["nodes"] = {"process": "material.stock"}

        all_kinds = set(base_config()["ports"])
        found = self._validate(patch, all_kinds - {"material_stock"})
        self.assertTrue(any("material_stock" in p and "kafka.lag" in p for p in found))

    def test_routed_kinds를_안_넘기면_이_검사를_건너뛴다(self):
        self.assertEqual(self._validate(lambda s: None, None), [])


class EndToEndTest(unittest.TestCase):
    def test_ports를_통해_실제_데이터가_흐른다(self):
        with temp_config(gbm=with_subgraph_patch(lambda c: None)) as cfg:
            state = run_graph(cfg)
        self.assertTrue(section(state, "material.stock").metrics)
        self.assertTrue(section(state, "alarm.trend").metrics)

    def test_매핑을_지우면_부팅에서_막힌다(self):
        def patch(cfg):
            cfg["ports"].pop("material_stock")

        with temp_config(gbm=with_subgraph_patch(patch)) as cfg:
            with self.assertRaises(ConfigValidationError) as ctx:
                run_graph(cfg)
        self.assertIn("material_stock", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
