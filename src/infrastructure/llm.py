"""LLM 어댑터 — 모든 LLM 호출이 지나는 단일 경로.

두 구현이 있고, config의 llm.adapter로 고른다.

    FakeLLMAdapter   규칙 기반 가짜 응답. 기본값. DB·LLM 없이 개발할 때.
    ChatModelAdapter 실제 LLM. ★ 외부와 통신하는 유일한 지점.

공통 로직(프롬프트 기록, replay, 환각 가드레일)은 BaseLLMAdapter에 있고,
하위 클래스는 **_complete()와 _judge_raw() 둘만** 구현한다. 그래서 "실제
LLM과 붙는 코드가 어디냐"는 질문의 답이 ChatModelAdapter의 그 두 메서드다.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any

from pydantic import BaseModel

from src.domain.models import Judgement, LLMTrace, Requirement, Severity

#: config의 llm.adapter 값
ADAPTER_FAKE = "fake"
ADAPTER_CHAT_MODEL = "chat_model"

#: init_chat_model이 아는 이름으로 바꿔준다. 사내 OpenAI 호환
#: 게이트웨이는 provider=openai + base_url 교체로 붙는다.
PROVIDER_ALIAS = {
    "openai_compatible": "openai",
    "azure_openai": "azure_openai",
}


class JudgementList(BaseModel):
    """structured output 스키마. LLM이 이 형태로 답하도록 강제한다."""

    judgements: list[Judgement]


# --------------------------------------------------------------------------
# 공통
# --------------------------------------------------------------------------
class BaseLLMAdapter:
    """호출 경로·기록·가드레일. 실제 통신은 하위 클래스가 한다."""

    def __init__(
        self,
        model: str,
        temperature: float = 0.0,
        replay: dict[str, str] | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self._replay = replay or {}
        self._traces: list[LLMTrace] = []
        self.guardrail_drops: list[str] = []

    # -- 하위 클래스가 구현하는 둘 -------------------------------------
    async def _complete(self, prompt: str) -> str:
        """프롬프트 → 자유 서술 문자열."""
        raise NotImplementedError

    async def _judge_raw(self, prompt: str, allowed_ids: list[str]) -> list[Judgement]:
        """프롬프트 → 구조화된 판정 목록. 검증 전 raw."""
        raise NotImplementedError

    async def _plan_raw(self, prompt: str, allowed: list[str]) -> Requirement:
        """프롬프트 → 구조화된 실행 계획. 검증 전 raw."""
        raise NotImplementedError

    # -- 공통 경로 -----------------------------------------------------
    def _record(self, node: str, prompt: str, response: str, replayed: bool = False):
        """변수 치환이 끝난 최종 프롬프트와 raw 응답을 남긴다.

        State에 쌓여 체크포인터에 저장되므로 별도 로깅 인프라가 없다.
        """
        self._traces.append(
            LLMTrace(
                node=node,
                prompt=prompt,
                response=response,
                model=self.model,
                temperature=self.temperature,
                replayed=replayed,
            )
        )

    def _replay_key(self, node: str, prompt: str) -> str:
        return f"{node}|{hash(prompt)}"

    async def narrate(self, node: str, prompt: str) -> str:
        key = self._replay_key(node, prompt)
        if key in self._replay:
            response = self._replay[key]
            self._record(node, prompt, response, replayed=True)
            return response

        response = await self._complete(prompt)
        self._record(node, prompt, response)
        return response

    async def judge(
        self, node: str, prompt: str, allowed_ids: list[str]
    ) -> list[Judgement]:
        """판정을 받아 근거를 코드로 검증한다.

        LLM이 든 evidence id가 실제 입력에 없으면 그 판정을 버린다.
        이 검증은 LLM 없이 돌기 때문에 항상 신뢰할 수 있다.

        trace에는 판정을 **JSON으로** 남긴다. "3건의 판정" 같은 요약만
        남기면 replay로 복원할 수 없고, 무엇을 근거로 뭘 판정했는지도
        나중에 확인할 수 없다.
        """
        if not allowed_ids:
            return []

        key = self._replay_key(node, prompt)
        if key in self._replay:
            payload = self._replay[key]
            raw = [Judgement(**j) for j in json.loads(payload)]
            self._record(node, prompt, payload, replayed=True)
        else:
            raw = await self._judge_raw(prompt, allowed_ids)
            payload = json.dumps(
                [j.model_dump(mode="json") for j in raw], ensure_ascii=False
            )
            self._record(node, prompt, payload)

        return self._enforce_evidence(node, raw, set(allowed_ids))

    async def plan(self, node: str, prompt: str, allowed: list[str]) -> Requirement:
        """질의를 실행 계획으로 바꾸고 선택된 이름을 코드로 대조한다.

        judge()가 evidence를 대조하는 것과 같은 구조다. 검사기가 LLM이 아니라
        집합 연산이므로 검사 자체가 틀릴 수 없다.
        """
        if not allowed:
            return Requirement(is_full_scope=True)

        key = self._replay_key(node, prompt)
        if key in self._replay:
            payload = self._replay[key]
            raw = Requirement(**json.loads(payload))
            self._record(node, prompt, payload, replayed=True)
        else:
            raw = await self._plan_raw(prompt, allowed)
            payload = json.dumps(raw.model_dump(mode="json"), ensure_ascii=False)
            self._record(node, prompt, payload)

        return self._enforce_selection(node, raw, set(allowed))

    def _enforce_selection(
        self, node: str, req: Requirement, allowed: set[str]
    ) -> Requirement:
        kept = [n for n in req.selected if n in allowed]
        dropped = [n for n in req.selected if n not in allowed]
        if dropped:
            self.guardrail_drops.append(
                f"{node}: 등록되지 않은 분석 {dropped!r}을 선택해 제외했습니다"
            )
        return req.model_copy(update={"selected": kept, "dropped": dropped})

    def _enforce_evidence(
        self, node: str, judgements: list[Judgement], allowed: set[str]
    ) -> list[Judgement]:
        kept: list[Judgement] = []
        for j in judgements:
            unknown = [e for e in j.evidence if e not in allowed]
            if unknown:
                # 실제 운영에서는 여기서 1회 재시도 후에도 실패하면 폐기한다.
                self.guardrail_drops.append(
                    f"{node}: 근거 {unknown!r}가 입력에 없어 판정을 폐기했습니다"
                )
                continue
            kept.append(j)
        return kept

    def drain_traces(self) -> list[LLMTrace]:
        """수집된 trace를 넘기고 비운다. 노드가 State에 실어 보낸다."""
        out, self._traces = self._traces, []
        return out

    def drain_guardrail_drops(self) -> list[str]:
        """폐기된 판정 기록을 넘기고 비운다.

        서브그래프마다 다른 LLM을 쓸 수 있으므로 어댑터에 쌓아두면 한곳에서
        모을 수 없다. trace와 마찬가지로 State로 올려보낸다.
        """
        out, self.guardrail_drops = self.guardrail_drops, []
        return out


# --------------------------------------------------------------------------
# 가짜 — 기본값
# --------------------------------------------------------------------------
class FakeLLMAdapter(BaseLLMAdapter):
    """규칙 기반 가짜 응답. 외부와 통신하지 않는다.

    - narrate: 프롬프트에 실린 사실('- '로 시작하는 줄)을 문장으로 엮는다
    - judge  : 허용된 id 안에서 판정을 만들되, 가드레일 동작을 보여주려고
               일부러 한 건은 없는 id를 근거로 댄다
    """

    def __init__(self, model: str = "fake-local", seed: str = "", **kw: Any) -> None:
        super().__init__(model=model, **kw)
        self._rng = random.Random(seed or model)

    async def _complete(self, prompt: str) -> str:
        facts = [
            re.sub(r"^\s*-\s*(\[[^\]]+\]\s*)?", "", ln).strip()
            for ln in prompt.splitlines()
            if ln.lstrip().startswith("- ")
        ]
        facts = [f for f in facts if f][:3]
        if not facts:
            return "특이사항이 확인되지 않았습니다."
        body = f"{facts[0]} 항목이 두드러집니다."
        if len(facts) > 1:
            body += f" 함께 {'; '.join(facts[1:])} 상태가 관측되었습니다."
        return body + " 지속 여부를 다음 주기에 재확인할 필요가 있습니다."

    async def _judge_raw(self, prompt: str, allowed_ids: list[str]) -> list[Judgement]:
        reasoning = await self._complete(prompt)
        out = []
        for idx, ev in enumerate(allowed_ids[: min(2, len(allowed_ids))]):
            out.append(
                Judgement(
                    subject=f"판정 #{idx + 1}",
                    severity=Severity.WARNING if idx == 0 else Severity.NORMAL,
                    reasoning=reasoning,
                    # 두 번째는 일부러 없는 id → 가드레일이 잡는다
                    evidence=[ev if idx == 0 else f"{ev}::hallucinated"],
                    confidence=0.8,
                )
            )
        return out

    async def _plan_raw(self, prompt: str, allowed: list[str]) -> Requirement:
        """프롬프트에 이름이 언급된 분석을 고른다.

        _judge_raw와 마찬가지로 **일부러 없는 이름을 하나 섞는다.** 가드레일이
        실제로 동작하는지 LLM 없이 확인할 수 있어야 하기 때문이다.
        """
        hits = [n for n in allowed if n in prompt or n.split(".")[-1] in prompt]
        picked = hits or allowed[:1]
        return Requirement(
            focus=[n.split(".")[-1] for n in picked],
            selected=[*picked, "nonexistent.analysis"],
            rationale="질의에 언급된 분석을 선택했습니다.",
        )


# --------------------------------------------------------------------------
# 실제 LLM — ★ 외부와 통신하는 유일한 지점
# --------------------------------------------------------------------------
class ChatModelAdapter(BaseLLMAdapter):
    """LangChain chat model 어댑터.

    config로 켠다:
        "llm": { "adapter": "chat_model", "provider": "openai_compatible",
                 "model": "gpt-4o-mini", "temperature": 0.0 }

    접속 정보는 .env에서 온다(LLM_BASE_URL, LLM_API_KEY). 사내 게이트웨이가
    OpenAI 호환이면 base_url 교체만으로 공급자가 바뀐다.

    langchain-openai 같은 공급자 패키지가 필요하므로 import를 지연시킨다.
    fake를 쓰는 동안에는 로드되지 않는다.
    """

    def __init__(
        self,
        model: str,
        provider: str = "openai_compatible",
        base_url: str = "",
        api_key: str = "",
        **kw: Any,
    ) -> None:
        super().__init__(model=model, **kw)
        self.provider = PROVIDER_ALIAS.get(provider, provider)
        self.base_url = base_url
        self.api_key = api_key
        self._client = None

    def _ensure_client(self):
        """★ 여기서 실제 클라이언트가 만들어진다."""
        if self._client is not None:
            return self._client

        try:
            from langchain.chat_models import init_chat_model
        except ImportError as exc:  # pragma: no cover - 환경 의존
            raise RuntimeError(
                "langchain이 설치되어 있지 않습니다. 공급자 패키지도 필요합니다 "
                "(예: pip install langchain langchain-openai)."
            ) from exc

        kwargs: dict[str, Any] = {"temperature": self.temperature}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        if self.api_key:
            kwargs["api_key"] = self.api_key

        self._client = init_chat_model(
            self.model, model_provider=self.provider, **kwargs
        )
        return self._client

    async def _complete(self, prompt: str) -> str:
        """★ 실제 호출 지점 (서술)."""
        message = await self._ensure_client().ainvoke(
            [{"role": "user", "content": prompt}]
        )
        return message.content if isinstance(message.content, str) else str(message.content)

    async def _judge_raw(self, prompt: str, allowed_ids: list[str]) -> list[Judgement]:
        """★ 실제 호출 지점 (판정).

        structured output으로 스키마를 강제하므로 파싱 코드가 필요 없고,
        형식이 어긋나면 LangChain이 재시도한다. 그래도 LLM이 없는 id를
        지어낼 수는 있으므로, 반환값은 judge()의 가드레일을 거친다.
        """
        allowed = "\n".join(f"  {i}" for i in allowed_ids)
        structured = self._ensure_client().with_structured_output(JudgementList)
        result = await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "당신은 제조 운영 데이터를 분석합니다. evidence에는 "
                        "아래 목록에 있는 id만 넣으세요. 목록에 없는 id를 "
                        "만들어내면 그 판정은 폐기됩니다.\n" + allowed
                    ),
                },
                {"role": "user", "content": prompt},
            ]
        )
        return result.judgements

    async def _plan_raw(self, prompt: str, allowed: list[str]) -> Requirement:
        """★ 실제 호출 지점 (실행 계획).

        도구 호출도 루프도 없는 단발 호출이다. 판단에 필요한 재료가 프롬프트에
        모두 들어 있으므로 반복할 이유가 없다.
        """
        listing = "\n".join(f"  {n}" for n in allowed)
        structured = self._ensure_client().with_structured_output(Requirement)
        return await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "당신은 제조 운영 리포트의 범위를 정합니다. selected에는 "
                        "아래 목록에 있는 이름만 넣으세요. 목록에 없는 이름을 "
                        "만들어내면 그 선택은 폐기됩니다.\n" + listing
                    ),
                },
                {"role": "user", "content": prompt},
            ]
        )


# --------------------------------------------------------------------------
def replay_map(traces: list[LLMTrace]) -> dict[str, str]:
    """저장된 trace를 replay 캐시 형태로 바꾼다.

    같은 노드에 같은 프롬프트가 오면 저장된 응답을 그대로 돌려주므로,
    LLM을 고정한 채 후속 로직만 고쳐가며 디버깅할 수 있다.
    """
    return {f"{t.node}|{hash(t.prompt)}": t.response for t in traces}


def build_llm(
    llm_cfg: dict,
    base_url: str = "",
    api_key: str = "",
    replay: dict[str, str] | None = None,
) -> BaseLLMAdapter:
    """config + .env → LLM 어댑터. Composition Root에서 호출한다."""
    adapter = llm_cfg.get("adapter", ADAPTER_FAKE)
    model = llm_cfg.get("model", "fake-local")
    temperature = llm_cfg.get("temperature", 0.0)

    if adapter == ADAPTER_FAKE:
        return FakeLLMAdapter(
            model=model,
            temperature=temperature,
            seed=llm_cfg.get("seed", ""),
            replay=replay,
        )
    if adapter == ADAPTER_CHAT_MODEL:
        return ChatModelAdapter(
            model=model,
            temperature=temperature,
            provider=llm_cfg.get("provider", "openai_compatible"),
            base_url=base_url,
            api_key=api_key,
            replay=replay,
        )
    raise ValueError(
        f"알 수 없는 llm.adapter '{adapter}'. "
        f"가능: {ADAPTER_FAKE}, {ADAPTER_CHAT_MODEL}"
    )
