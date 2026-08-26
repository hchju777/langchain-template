"""질의에 따른 라우팅. 그래프 규약을 본다.

질의가 없을 때 지금과 동일하게 도는지가 가장 중요한 회귀 항목이다.
"""

from __future__ import annotations

import unittest

from tests.helpers import run_graph, temp_config, with_subgraph_patch


def _cfg():
    return with_subgraph_patch(lambda c: None)


class QueryRoutingTest(unittest.TestCase):
    def test_no_query_runs_every_enabled_subgraph(self):
        with temp_config(gbm=_cfg()) as cfg:
            state = run_graph(cfg)
        keys = {s.key for s in state["sections"]}
        self.assertIn("material.stock", keys)
        self.assertIn("kpi.check", keys)
        self.assertGreaterEqual(len(keys), 5, f"전체가 돌아야 한다: {keys}")

    def test_no_query_leaves_requirement_full_scope(self):
        with temp_config(gbm=_cfg()) as cfg:
            state = run_graph(cfg)
        self.assertTrue(state["requirement"].is_full_scope)

    def test_query_runs_only_selected_subgraphs(self):
        """Fake 어댑터는 프롬프트에 이름이 있는 분석을 고른다."""
        with temp_config(gbm=_cfg()) as cfg:
            state = run_graph(cfg, query="material.stock 만 보여줘")
        self.assertEqual({s.key for s in state["sections"]}, {"material.stock"})

    def test_aggregate_runs_even_when_some_subgraphs_are_skipped(self):
        """일부만 스케줄돼도 취합 barrier가 막히지 않아야 한다."""
        with temp_config(gbm=_cfg()) as cfg:
            state = run_graph(cfg, query="material.stock 만 보여줘")
        self.assertIsNotNone(state["overall"])
        self.assertTrue(state["rendered"])
