# langchain-template

여러 비동기 데이터 소스에서 운영 지표를 수집·분석해 md 리포트와 메일로 내보내는 **LangGraph 기반 템플릿**.

특정 서비스 하나를 위한 앱이 아니라, GBM/FCT(사업부/공장)마다 **config만 바꿔 찍어내는 골격**입니다. 분석 대상 도메인은 고정되어 있지 않습니다.

**처음이시면 [튜토리얼](docs/tutorial.md)부터 보세요** — Redis에서 데이터를 가져와 로직을 돌리고 리포트 섹션으로 내보내기까지 15분짜리 실습입니다.
**하려는 일이 이미 정해졌으면 [작업별 가이드](docs/howto.md)**에서 바로 찾으세요.

| 문서 | 답하는 질문 |
|---|---|
| [튜토리얼](docs/tutorial.md) | "처음인데 어떻게 시작하지?" — 새 분석 만들기 실습 |
| [작업별 가이드](docs/howto.md) | "이걸 하려면 어떻게 하지?" — 작업별 색인 |
| [아키텍처](docs/architecture.md) | **"실행하면 무슨 일이 일어나지?"** — 실행 흐름·컴포넌트 책임 |
| [config 레퍼런스](docs/config-reference.md) | "이 설정이 뭐지?" — 모든 항목의 의미·기본값 |
| [LLM 규약과 연결](docs/llm.md) | "LLM을 어디에 쓰고 어떻게 붙이지?" |
| [용어집·판단 가이드](docs/glossary.md) | "언제 뭘 고르지?" — 용어와 판단 기준 |
| [실제 연결로 전환](docs/going-live.md) | "스텁을 실제 DB로 어떻게 바꾸지?" (⚠ 미검증) |
| [테스트](tests/README.md) | "테스트를 어떻게 쓰고 돌리지?" |
| [설계 문서](docs/superpowers/specs/2026-08-13-langgraph-report-template-design.md) | "왜 이렇게 설계했지?" — 검토했다 버린 대안들 |
| [ref/](ref/README.md) | "LangGraph 자체가 왜 이렇게 생겼지?" |

위 아홉은 **이 템플릿 사용법**이고, `ref/`는 **LangGraph 문법·개념** 자체입니다. 템플릿 규약(4슬롯, `Command` 라우팅, `as_of` 주입)이 왜 그런지 궁금해지면 `ref/`로 내려가세요.

---

## 빠른 시작

### 설치

```bash
python -m venv .venv
```

| OS / 셸 | 활성화 |
|---|---|
| Linux / macOS | `source .venv/bin/activate` |
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows cmd | `.venv\Scripts\activate.bat` |

```bash
pip install -r requirements.txt                        # 실행용
pip install -r requirements.txt -r requirements-dev.txt  # 개발·테스트 포함
```

> **Windows 주의 — `tzdata`**: `requirements.txt`에 포함돼 있습니다. `zoneinfo`가 시스템 tz 데이터베이스를 찾는데 Windows에는 없어서, 없으면 config의 `"timezone": "Asia/Seoul"`이 실패합니다.
>
> 모든 의존성은 **미리 빌드된 휠**로 설치되어 컴파일러가 필요 없습니다.

> PowerShell에서 실행 정책 오류가 나면 그 세션에서만 풀어주면 됩니다:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`

**활성화하면 이후 명령은 모든 OS에서 같습니다.**

```bash
# 등록된 서브그래프 목록
python -m src registry

# 병합된 config와 각 값의 출처
python -m src config show --gbm mx --factory gumi

# 리포트 생성
python -m src run --gbm mx --factory gumi

# 어제 것 다시 뽑기 (같은 as_of는 항상 같은 결과)
python -m src run --gbm mx --factory gumi --as-of 2026-08-12T08:00

# 노드별 진행 표시
python -m src run --gbm mx --factory gumi --stream
```

활성화 없이 바로 쓰려면 인터프리터를 직접 지정합니다.

```bash
.venv/bin/python -m src registry          # Linux / macOS
.venv\Scripts\python.exe -m src registry  # Windows
```

리포트는 `output/{gbm}_{factory}_{as_of}.md`로 저장됩니다.

> **Windows 주의**: 리포트에 심각도 표시로 이모지(🔴🟡🟢)를 씁니다. 구형 `cmd`에서 깨지면 `chcp 65001`로 UTF-8을 켜거나 Windows Terminal / PowerShell을 쓰세요. 파일로 저장된 md는 영향받지 않습니다.

---

## 지금 도는 것

예시로 6개 분석이 구현되어 있습니다. 모두 **병렬 실행**되고 마지막에 취합됩니다.

| 등록명 | 하는 일 | 데이터 소스 | 시간 성격 |
|---|---|---|---|
| `health.connectivity` | Redis/MongoDB/Kafka/REST 접속 확인 | 4개 어댑터 `ping()` | 스냅샷 |
| `kafka.lag` | 컨슈머 그룹 lag, 파티션 편중 | Kafka Admin (오프셋만) | 스냅샷 |
| `line.equipment` | 라인별 장비 상태 + 생산 달성률 | Redis 3종 결합 | 스냅샷 |
| `kpi.check` | KPI 목표 대비 이탈 + LLM 조합 판단 | REST | 스냅샷 |
| `alarm.trend` | 최근 7일 scen_id별 알람 추세 | MongoDB | **구간** |
| `material.stock` | 자재 소진 예상 시간 | Redis | 스냅샷 |

마지막 `material.stock`은 [튜토리얼](docs/tutorial.md)에서 처음부터 만들어보는 예시입니다.

> **DB·LLM 연결은 스텁입니다.** 실제 호출부는 주석으로 위치만 표시하고 지금은 in-memory dict로 대신합니다. 바꾸는 방법은 [실제 연결로 전환](#실제-연결로-전환)을 보세요.

---

## 구조

**4계층이고 의존 방향은 안쪽으로만 향합니다. `domain`은 바깥을 모릅니다.**

```
      presentation                                infrastructure
   (사람과 맞닿는 면)                              (외부 세계 I/O)
   ┌──────────────┐                            ┌──────────────────┐
   │ CLI          │──→  ┌─────────────┐   ──→  │ Mongo/Redis/REST │
   │ (controller) │     │ application │        │ Kafka Admin, LLM │
   ├──────────────┤     │  run_report │   ──→  │ 파일 쓰기, SMTP   │
   │ renderers    │←──  │      ↓      │        │ 체크포인터·락     │
   │ templates    │     │   domain    │   ──→  │ Scheduler ───┐   │
   │ (presenter)  │     └─────────────┘        └──────────────│───┘
   └──────────────┘            ↑                              │
                               └──────────────────────────────┘
                          스케줄러도 같은 유스케이스를 부른다
```

| 계층 | 무엇 |
|---|---|
| `domain/` | 모델과 포트(`Protocol`). 바깥을 모릅니다 |
| `application/` | 유스케이스 — `run_report`, 그래프 조립, 서브그래프 |
| `presentation/` | 사람과 맞닿는 면 — CLI(controller), 렌더러(presenter), 템플릿 |
| `infrastructure/` | 외부 I/O — 저장소·LLM·발송·체크포인터·락·스케줄러 |
| `config/`, `.env` | 설정. **GBM/FCT축**과 **환경축**으로 갈립니다 |

**계층 경계의 판단 기준, 실행 한 번의 전 과정, 컴포넌트별 책임은 → [아키텍처](docs/architecture.md)**

---

## 핵심 규약

새 코드를 쓸 때 지켜야 할 것들입니다. 이유는 [아키텍처](docs/architecture.md#6-핵심-규약-요약)에 있습니다.

| 규약 | 한 줄 이유 |
|---|---|
| `as_of`를 밖에서 주입 | 재현성·재개·Time Travel·락이 전부 여기 걸림 |
| 숫자는 `Metric`에, LLM은 서술만 | LLM이 숫자를 옮기면 언젠가 틀리게 씀 |
| 판정에 실제 `Record.id`를 근거로 | 역추적 + 환각 가드레일 |
| config는 조립 시점에만 | 노드 시그니처가 요구사항을 문서화 |
| 슬롯 간 `Command`만 | 정적 엣지와 겹치면 실패 격리가 깨짐 |
| 부팅에서 막을 수 있으면 막는다 | 새벽 배치에서 발견되면 늦음 |

### 3단 config 병합

```
config/gbm/{gbm}.json  →  factories/{factory}/common.json  →  factories/{factory}/{gbm}.json
```

뒤가 앞을 덮어쓰고, 중첩 객체는 재귀 병합되므로 **바꿀 키만** 적으면 됩니다. 값마다 출처가 기록되어 `config show`로 확인할 수 있습니다.

**비밀값과 접속 주소는 `.env`입니다** — JSON은 커밋되니까요. 자세한 건 → [config 레퍼런스](docs/config-reference.md)

### 서브그래프 4슬롯

```
validate_input ──→ process ──→ generate_output ──→ (ReportSection)
      │ 실패          │ 실패          │ 실패
      └──────────────┴──────────────┴──→ handle_error ──→ (degraded 섹션)
```

**한 분석의 실패가 리포트 전체를 죽이지 않습니다.** 보통 `process`만 구현하면 됩니다.

---

## 새 분석 추가하기

**파일 1개 + config 2줄**입니다. 단계별 실습은 → [튜토리얼](docs/tutorial.md)

```python
# src/application/subgraphs/quality/defect.py   → 등록명 "quality.defect"
class DefectConfig(SubgraphConfig):
    threshold_ppm: int = 500

@register()
class Defect(BaseSubgraph):
    title = "불량률 점검"
    config_model = DefectConfig
    context_type = SnapshotContext     # 구간이면 HistoricalContext
    required_kinds = ("defect",)       # ports에 매핑이 있어야 한다

    async def process(self, state: SubgraphState) -> dict:
        records = await self.deps.data.fetch(state.scoped, FetchSpec(kind="defect"))
        ...
        return {"records": records, "metrics": metrics, "judgements": judgements}
```

```json
{
  "subgraphs": { "quality.defect": { "enabled": true, "threshold_ppm": 300 } },
  "ports":     { "defect": "rest" }
}
```

리포트 섹션은 자동으로 생기고 템플릿은 고칠 게 없습니다.

---

## LLM

취합과 각 분석 양쪽에서 쓰되 **규칙으로 쓸 수 있으면 코드가 이깁니다.** 네 가지를 강제합니다 — 숫자는 LLM이 다시 쓰지 않고, 판정에는 실제 `Record.id`를 근거로 달고, 그 근거를 코드로 검증하고(환각 가드레일), 프롬프트·응답 원문을 State에 남깁니다.

실제 모델로 바꾸는 건 **config 한 줄**입니다.

```json
{ "llm": { "adapter": "chat_model", "provider": "openai_compatible", "model": "gpt-4o-mini" } }
```

규약·연결·모델 교체·replay → **[LLM 규약과 연결](docs/llm.md)**

---

## 데이터 흐름

```
CLI ─→ BaseContext(as_of 확정) ─→ RunLock ─→ run_report
        │
        ├→ health.connectivity ┐
        ├→ kafka.lag           │
        ├→ line.equipment      │
        ├→ kpi.check           ├→ aggregate ─→ render ─→ deliver
        ├→ alarm.trend         │   (집계는     (md 문자열)  (파일/메일,
        └→ material.stock      ┘    코드가                  멱등키 확인)
             각각 4슬롯              LLM은 해석)
             ReportSection 반환
```

LangGraph가 fan-out을 자동 병렬 실행하고, 취합 노드는 전부 끝나야 도는 자연스러운 barrier가 됩니다.

**각 단계에서 어느 컴포넌트가 무엇을 하는지는 → [아키텍처 §2](docs/architecture.md#2-한-번-실행의-전-과정)**

---
## CLI

| 명령 | 설명 |
|---|---|
| `run --gbm --factory [--as-of] [--stream] [--quiet]` | 리포트 생성 |
| `run ... --save-traces PATH` | LLM 프롬프트·응답을 JSON으로 저장 |
| `run ... --replay PATH` | 저장된 응답을 재생 (LLM 고정) |
| `run ... --show-checkpoints` | 실행 후 체크포인트 목록 |
| `scheduler --gbm --factory [--once]` | 상주 모드로 cron 실행 |
| `config show --gbm --factory` | 병합 결과 + 각 값의 출처 |
| `registry` | 등록된 서브그래프·노드 부품 목록 |

`--gbm`/`--factory`를 생략하면 `.env`의 `DEPLOY_GBM`/`DEPLOY_FACTORY`를 씁니다.

---

## 실제 연결로 전환

바꿔야 할 곳은 **네 군데**뿐이고, **서브그래프 코드는 하나도 안 바뀝니다.**

| 무엇 | 어디 | 바꾸는 것 |
|---|---|---|
| 저장소 조회 | `src/infrastructure/stores.py` | `fake_data` 호출 → 실제 쿼리 |
| 접속 정보 | `.env` | 주소·계정·비밀번호 |
| LLM | config `llm.adapter` | `"fake"` → `"chat_model"` |
| 체크포인터 | config `checkpoint.backend` | `"memory"` → `"mongodb"` |

뒤의 둘은 **코드를 고칠 필요가 없습니다** — 실제 어댑터가 이미 구현돼 있어 config에서 고르기만 하면 됩니다.

```json
{
  "llm": { "adapter": "chat_model", "provider": "openai_compatible", "model": "gpt-4o-mini" },
  "checkpoint": { "backend": "mongodb" }
}
```

드라이버별 상세 절차(커넥션 수명, `Decimal128`·`ObjectId` 처리, Kafka 오프셋 조회, 전환 순서)는 **[실제 연결로 전환](docs/going-live.md)**에 있습니다.

> **렌더러**: 지금은 표준 라이브러리(`string.Template`)로 템플릿을 채웁니다. 반복문·조건문이 필요해지면 Jinja2 어댑터를 추가하고 `Dependencies`에서 바꿔 끼우면 됩니다 — `ReportRendererPort` 뒤에 있어 노드와 config 사용법은 그대로입니다.

---

## 아직 없는 것

| | 상태 | 비고 |
|---|---|---|
| 어댑터 테스트 | **의도적으로 없음** | 나머지 4개 층은 [tests/](tests/README.md)에 있습니다. 어댑터만 안 만들기로 한 이유는 [설계 문서 §11](docs/superpowers/specs/2026-08-13-langgraph-report-template-design.md) |
| 분산 락 | 같은 호스트만 | 파일 락(`.locks/`)이라 여러 호스트에 스케줄러를 띄우면 겹칠 수 있습니다. Mongo 유니크 인덱스 같은 공유 저장소 락이 필요 |

`fake_data.py`는 `as_of`로 난수 시드를 고정하므로 **같은 `as_of`는 항상 같은 데이터**를 돌려줍니다. 실제 연결 전에도 멱등성을 확인할 수 있습니다.

---

## 요구 환경

- **Python 3.12** 이상 (3.10+에서 동작하지만 3.12로 개발·검증했습니다)
- Windows / Linux / macOS — 전부 미리 빌드된 휠로 설치됩니다

의존성은 [requirements.txt](requirements.txt)에 용도별로 정리돼 있습니다.

| 묶음 | 언제 필요한가 |
|---|---|
| 코어 (langgraph, langchain, pydantic) | 항상 |
| `jinja2` | 반복문·조건문이 있는 템플릿을 쓸 때. 지금은 표준 라이브러리로 렌더링 |
| `APScheduler`, `tzdata` | 상주 스케줄러. **`tzdata`는 Windows 필수** |
| `langchain-openai` | `llm.adapter`가 `"chat_model"`일 때 |
| `redis`, `pymongo`, `aiokafka`, `httpx`, `aiosmtplib` | 실제 저장소·메일에 연결할 때 |
| `langgraph-checkpoint-mongodb` | 재시작 후 재개가 필요할 때 (`checkpoint.backend: "mongodb"`) |

> **MongoDB 드라이버**: `motor`가 아니라 **PyMongo의 `AsyncMongoClient`**를 씁니다. motor는 2026-05-14에 deprecated 되었습니다(중요 버그 수정만 2027-05까지).

지금 상태로는 **코어만 있어도 전부 동작합니다** — DB·LLM이 스텁이고 테스트가 `unittest` 기반이라서요.
