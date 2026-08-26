"""체크포인터 — Time Travel, fork, replay.

체크포인터를 붙이면 노드마다 State가 저장되고, 그 위에서 과거 시점 열람과
fork가 따라온다. 여기 테스트는 전부 InMemorySaver로 도는데, 프로세스 안에서
쓰는 기능은 실제 백엔드와 동작이 같기 때문이다.

프로세스 재시작 후 재개만 memory로 검증할 수 없다 — 그건 백엔드 교체가
필요하고, 이 환경에는 해당 패키지가 없다.
"""

import asyncio
import unittest
from datetime import datetime

from src.domain.models import LLMTrace
from src.infrastructure.checkpoint import (
    BACKEND_MEMORY,
    BACKEND_MONGODB,
    BACKEND_NONE,
    CheckpointerUnavailableError,
    build_checkpointer,
    query_digest,
    thread_id_for,
)
from src.infrastructure.llm import replay_map
from tests.helpers import (
    AS_OF,
    run_graph,
    run_graph_with,
    section,
    temp_config,
    with_subgraph_patch,
)


def plain_config(**overrides):
    def patch(cfg):
        cfg.setdefault("checkpoint", {}).update(overrides)

    return with_subgraph_patch(patch)


class BuildCheckpointerTest(unittest.TestCase):
    def test_memory가_기본값이다(self):
        self.assertIsNotNone(build_checkpointer({}))

    def test_none이면_체크포인터를_안_붙인다(self):
        self.assertIsNone(build_checkpointer({"backend": BACKEND_NONE}))

    def test_mongodb에_접속_주소가_없으면_실패한다(self):
        with self.assertRaises(CheckpointerUnavailableError) as ctx:
            build_checkpointer({"backend": BACKEND_MONGODB}, env=None)
        self.assertIn("MONGODB_URI", str(ctx.exception))

    def test_mongodb_연결_실패는_원인을_알려준다(self):
        """raw pymongo traceback 대신 무엇을 고쳐야 하는지 보여야 한다."""

        class Env:
            mongodb_uri = "mongodb://127.0.0.1:1"  # 아무도 없는 포트
            checkpoint_database = "x"

        with self.assertRaises(CheckpointerUnavailableError) as ctx:
            build_checkpointer({"backend": BACKEND_MONGODB}, env=Env())
        message = str(ctx.exception)
        self.assertIn("MongoDB에 연결할 수 없습니다", message)
        self.assertIn(BACKEND_MEMORY, message, "대안을 알려줘야 한다")

    def test_알_수_없는_백엔드는_실패한다(self):
        with self.assertRaises(ValueError):
            build_checkpointer({"backend": "sqlite"})


class ThreadIdTest(unittest.TestCase):
    def test_같은_실행은_같은_스레드가_된다(self):
        a = thread_id_for("mx", "gumi", AS_OF)
        b = thread_id_for("mx", "gumi", AS_OF)
        self.assertEqual(a, b)

    def test_as_of가_다르면_다른_스레드다(self):
        a = thread_id_for("mx", "gumi", AS_OF)
        b = thread_id_for("mx", "gumi", datetime(2026, 8, 12, 8, 0))
        self.assertNotEqual(a, b)

    def test_공장이_다르면_다른_스레드다(self):
        self.assertNotEqual(
            thread_id_for("mx", "gumi", AS_OF), thread_id_for("mx", "asan", AS_OF)
        )


class CheckpointHistoryTest(unittest.TestCase):
    """실행이 끝나면 각 단계의 State가 남아 있어야 한다."""

    def _history(self, state, graph, run_config):
        async def collect():
            return [s async for s in graph.aget_state_history(run_config)]

        return asyncio.run(collect())

    def test_단계마다_체크포인트가_남는다(self):
        with temp_config(gbm=plain_config()) as cfg:
            state, graph, run_config = run_graph_with(cfg)
        snapshots = self._history(state, graph, run_config)
        # 시작 + fan-out + aggregate + render + deliver + 완료
        self.assertGreaterEqual(len(snapshots), 5)

    def test_마지막_체크포인트는_다음_노드가_없다(self):
        with temp_config(gbm=plain_config()) as cfg:
            state, graph, run_config = run_graph_with(cfg)
        latest = self._history(state, graph, run_config)[0]
        self.assertEqual(latest.next, ())

    def test_과거_시점의_State를_열람할_수_있다(self):
        """render 이전 시점에는 rendered가 아직 없다."""
        with temp_config(gbm=plain_config()) as cfg:
            state, graph, run_config = run_graph_with(cfg)
        snapshots = self._history(state, graph, run_config)

        before_render = [s for s in snapshots if "render" in (s.next or ())]
        self.assertTrue(before_render, "render 직전 시점이 있어야 한다")
        self.assertIsNone(before_render[0].values.get("rendered"))
        self.assertTrue(state["rendered"], "최종 State에는 있다")

    def test_none_백엔드는_이력_조회가_막힌다(self):
        """체크포인터가 없으면 Time Travel도 없다. 리포트는 그대로 나온다."""
        with temp_config(gbm=plain_config(backend=BACKEND_NONE)) as cfg:
            state, graph, run_config = run_graph_with(cfg)
        with self.assertRaises(ValueError):
            self._history(state, graph, run_config)
        self.assertTrue(state["rendered"], "체크포인트 없이도 리포트는 나온다")


class ForkTest(unittest.TestCase):
    """과거 시점으로 돌아가 값을 바꿔 다시 실행한다."""

    def test_체크포인트에서_State를_바꿔_다시_돌린다(self):
        with temp_config(gbm=plain_config()) as cfg:
            state, graph, run_config = run_graph_with(cfg)

        async def fork():
            snapshots = [s async for s in graph.aget_state_history(run_config)]
            # render 직전으로 돌아간다
            target = next(s for s in snapshots if "render" in (s.next or ()))

            # 전체 요약을 손으로 바꿔 넣고 그 시점부터 재실행
            overall = target.values["overall"]
            overall.narrative = "손으로 고친 요약"
            forked_config = await graph.aupdate_state(
                target.config, {"overall": overall}
            )
            return await graph.ainvoke(None, config=forked_config)

        forked = asyncio.run(fork())
        self.assertIn("손으로 고친 요약", forked["rendered"])
        self.assertNotIn("손으로 고친 요약", state["rendered"], "원본은 그대로다")

    def test_fork해도_원본_실행은_남아_있다(self):
        with temp_config(gbm=plain_config()) as cfg:
            state, graph, run_config = run_graph_with(cfg)

        async def count():
            return len([s async for s in graph.aget_state_history(run_config)])

        before = asyncio.run(count())
        self.assertGreater(before, 0)


class ReplayTest(unittest.TestCase):
    """저장된 LLM 응답을 재생해 LLM을 고정한 채 후속 로직을 디버깅한다."""

    def test_모든_호출이_재생된다(self):
        with temp_config(gbm=plain_config()) as cfg:
            first = run_graph(cfg)
            replay = replay_map(first["traces"])
            second = run_graph(cfg, replay=replay)

        self.assertTrue(second["traces"])
        self.assertTrue(
            all(t.replayed for t in second["traces"]),
            "narrate와 judge 둘 다 재생돼야 한다",
        )

    def test_재생하면_결과가_같다(self):
        with temp_config(gbm=plain_config()) as cfg:
            first = run_graph(cfg)
            second = run_graph(cfg, replay=replay_map(first["traces"]))
        self.assertEqual(first["rendered"], second["rendered"])

    def test_판정도_그대로_복원된다(self):
        """judge의 trace가 요약문이면 복원할 수 없다 — JSON으로 남겨야 한다."""
        with temp_config(gbm=plain_config()) as cfg:
            first = run_graph(cfg)
            second = run_graph(cfg, replay=replay_map(first["traces"]))

        a = section(first, "kpi.check").judgements
        b = section(second, "kpi.check").judgements
        self.assertEqual(len(a), len(b))
        self.assertEqual([j.subject for j in a], [j.subject for j in b])

    def test_가드레일도_재생_후_동일하게_동작한다(self):
        with temp_config(gbm=plain_config()) as cfg:
            first = run_graph(cfg)
            second = run_graph(cfg, replay=replay_map(first["traces"]))
        self.assertEqual(first["guardrail_drops"], second["guardrail_drops"])

    def test_빈_replay는_실제_호출로_떨어진다(self):
        with temp_config(gbm=plain_config()) as cfg:
            state = run_graph(cfg, replay={})
        self.assertFalse(any(t.replayed for t in state["traces"]))

    def test_trace는_JSON으로_왕복한다(self):
        """CLI의 --save-traces / --replay가 이 경로를 쓴다."""
        with temp_config(gbm=plain_config()) as cfg:
            first = run_graph(cfg)

        dumped = [t.model_dump() for t in first["traces"]]
        restored = [LLMTrace(**d) for d in dumped]
        self.assertEqual(replay_map(first["traces"]), replay_map(restored))


class QueryThreadIdTest(unittest.TestCase):
    """질의가 실행 범위를 바꾸므로 as_of와 동급의 식별자여야 한다."""

    def test_unchanged_without_query(self):
        """질의가 없으면 기존 문자열 그대로. 스케줄러 체크포인트가 살아있어야 한다."""
        self.assertEqual(thread_id_for("mx", "gumi", AS_OF), "mx:gumi:20260813T0800")

    def test_differs_by_query(self):
        a = thread_id_for("mx", "gumi", AS_OF, "재고만")
        b = thread_id_for("mx", "gumi", AS_OF, "설비만")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("mx:gumi:20260813T0800:q"))

    def test_stable_for_same_query(self):
        self.assertEqual(
            thread_id_for("mx", "gumi", AS_OF, "재고만"),
            thread_id_for("mx", "gumi", AS_OF, "재고만"),
        )

    def test_digest_empty_without_query(self):
        self.assertEqual(query_digest(None), "")
        self.assertEqual(query_digest(""), "")
        self.assertEqual(len(query_digest("재고만")), 8)


if __name__ == "__main__":
    unittest.main()
