"""3단 config 병합과 출처 추적.

여기가 틀리면 "이 값이 왜 이래?"를 아무도 답할 수 없게 된다.
"""

import unittest

from src.config.loader import deep_merge
from src.constants import (
    CONFIG_LAYER_FACTORY_COMMON,
    CONFIG_LAYER_FACTORY_GBM,
    CONFIG_LAYER_GBM,
)
from tests.helpers import temp_config


class DeepMergeTest(unittest.TestCase):
    def test_뒤쪽_값이_앞쪽을_덮어쓴다(self):
        self.assertEqual(deep_merge({"a": 1}, {"a": 2}), {"a": 2})

    def test_중첩_dict는_재귀적으로_병합된다(self):
        merged = deep_merge(
            {"x": {"keep": 1, "override": "old"}},
            {"x": {"override": "new"}},
        )
        self.assertEqual(merged, {"x": {"keep": 1, "override": "new"}})

    def test_리스트는_병합하지_않고_교체한다(self):
        merged = deep_merge({"items": [1, 2, 3]}, {"items": [9]})
        self.assertEqual(merged["items"], [9])

    def test_원본을_변경하지_않는다(self):
        base = {"x": {"a": 1}}
        deep_merge(base, {"x": {"b": 2}})
        self.assertEqual(base, {"x": {"a": 1}})


class ThreeLayerMergeTest(unittest.TestCase):
    def test_세_계층이_순서대로_덮어쓴다(self):
        with temp_config(
            gbm={"v": "gbm", "only_gbm": 1},
            factory_common={"v": "common", "only_common": 2},
            factory_gbm={"v": "factory_gbm"},
        ) as cfg:
            self.assertEqual(cfg.get("v"), "factory_gbm")
            self.assertEqual(cfg.get("only_gbm"), 1)
            self.assertEqual(cfg.get("only_common"), 2)

    def test_없는_계층은_건너뛴다(self):
        with temp_config(gbm={"v": "gbm"}) as cfg:
            self.assertEqual(cfg.get("v"), "gbm")

    def test_config가_아예_없어도_뜬다(self):
        with temp_config() as cfg:
            self.assertEqual(cfg.data, {})
            self.assertIsNone(cfg.get("없는키"))

    def test_기본값을_돌려준다(self):
        with temp_config(gbm={}) as cfg:
            self.assertEqual(cfg.get("a", "b", default="fallback"), "fallback")

    def test_중간_경로가_dict가_아니면_기본값(self):
        with temp_config(gbm={"a": 5}) as cfg:
            self.assertEqual(cfg.get("a", "b", default="fallback"), "fallback")

    def test_section은_항상_dict를_돌려준다(self):
        with temp_config(gbm={"scalar": 1}) as cfg:
            self.assertEqual(cfg.section("scalar"), {})
            self.assertEqual(cfg.section("없음"), {})


class OriginTrackingTest(unittest.TestCase):
    """값마다 어느 계층에서 왔는지 — config show가 이걸 보여준다."""

    def test_덮어쓴_계층이_출처로_남는다(self):
        with temp_config(
            gbm={"nested": {"a": 1, "b": 2}},
            factory_common={"nested": {"b": 20}},
            factory_gbm={"nested": {"a": 100}},
        ) as cfg:
            self.assertEqual(cfg.origin_of("nested.a"), CONFIG_LAYER_FACTORY_GBM)
            self.assertEqual(cfg.origin_of("nested.b"), CONFIG_LAYER_FACTORY_COMMON)

    def test_한_계층에만_있으면_그_계층이_출처(self):
        with temp_config(gbm={"x": 1}) as cfg:
            self.assertEqual(cfg.origin_of("x"), CONFIG_LAYER_GBM)

    def test_없는_키는_기본값으로_표시된다(self):
        with temp_config(gbm={}) as cfg:
            self.assertEqual(cfg.origin_of("없음"), "(기본값)")

    def test_계층_파일_경로가_기록된다(self):
        with temp_config(gbm={}, factory_common={}) as cfg:
            self.assertTrue(cfg.layer_files[CONFIG_LAYER_GBM].exists())
            self.assertTrue(cfg.layer_files[CONFIG_LAYER_FACTORY_COMMON].exists())
            # 만들지 않은 계층은 경로만 있고 파일은 없다
            self.assertFalse(cfg.layer_files[CONFIG_LAYER_FACTORY_GBM].exists())


if __name__ == "__main__":
    unittest.main()
