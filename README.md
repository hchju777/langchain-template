# langchain-template

여러 비동기 데이터 소스에서 운영 지표를 수집·분석해 md 리포트와 메일로 내보내는 **LangGraph 기반 템플릿**.

특정 서비스 하나를 위한 앱이 아니라, GBM/FCT(사업부/공장)마다 **config만 바꿔 찍어내는 골격**입니다. 분석 대상 도메인은 고정되어 있지 않습니다.

**처음이시면 [튜토리얼](docs/tutorial.md)부터 보세요** — Redis에서 데이터를 가져와 로직을 돌리고 리포트 섹션으로 내보내기까지 15분짜리 실습입니다.

| 문서 | 내용 |
|---|---|
| [튜토리얼](docs/tutorial.md) | 새 분석을 처음부터 만들어보는 실습 |
| [config 레퍼런스](docs/config-reference.md) | 모든 설정 항목의 의미·기본값·어느 계층에 둘지 |
| [테스트](tests/README.md) | 실행 방법과 새 분석 테스트 작성법 |
| [설계 문서](docs/superpowers/specs/2026-08-13-langgraph-report-template-design.md) | 왜 이렇게 설계했는지, 검토했다 버린 대안들 |

---

## 빠른 시작

### 가상환경 활성화

| OS / 셸 | 명령 |
|---|---|
| Linux / macOS | `source .venv/bin/activate` |
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows cmd | `.venv\Scripts\activate.bat` |

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

**4계층입니다. 의존 방향은 안쪽으로만 향하고, `domain`은 바깥을 모릅니다.**

```
      presentation                                infrastructure
   (사람과 맞닿는 면)                              (외부 세계 I/O)
   ┌──────────────┐                            ┌──────────────────┐
   │ CLI          │──→  ┌─────────────┐   ──→  │ Mongo/Redis/REST │
   │ (controller) │     │ application │        │ Kafka Admin, LLM │
   ├──────────────┤     │      ↓      │   ──→  │ 파일 쓰기, SMTP   │
   │ renderers    │←──  │   domain    │        │ (Scheduler)      │
   │ templates    │     └─────────────┘        └──────────────────┘
   │ (presenter)  │
   └──────────────┘
```

```
src/
├── domain/                     ← 바깥을 모르는 순수 계층
│   ├── models.py                 Context 계열, Record, Judgement, ReportSection
│   ├── ports.py                  Protocol 정의 (구현은 바깥 두 계층에)
│   └── reducers.py               커스텀 리듀서 — 한곳에 모은다
│
├── application/                ← 유스케이스
│   ├── graph/
│   │   ├── state.py              부모 ReportState, Dependencies
│   │   ├── builder.py            Composition Root — config를 보는 유일한 곳
│   │   └── aggregate.py          취합 → 렌더 → 발송 노드
│   ├── subgraphs/              ← 자동 스캔 대상. 경로가 곧 등록명
│   │   ├── base.py               BaseSubgraph (4슬롯)
│   │   ├── health/connectivity.py    → "health.connectivity"
│   │   ├── kafka/lag.py              → "kafka.lag"
│   │   ├── line/equipment.py         → "line.equipment"
│   │   ├── kpi/check.py              → "kpi.check"
│   │   ├── alarm/trend.py            → "alarm.trend"
│   │   ├── material/stock.py         → "material.stock"
│   │   └── material/stock_gumi.py    → "material.stock_gumi" (슬롯 전용)
│   ├── nodes/                  ← 공유 슬롯 부품
│   │   └── outputs.py            "outputs.no_llm"
│   └── decorators.py             with_timing / with_error_handling / with_cache
│
├── presentation/               ← 무엇을 어떻게 보여줄 것인가
│   ├── cli.py                    인자 → BaseContext (controller), 결과 표시
│   ├── renderers.py              ReportSection → md 문자열 (presenter)
│   └── templates/                리포트 양식. config로 교체
│       ├── report.md               기본 (표 + 판정 근거 전부)
│       └── report_brief.md         요약본 (한 줄 리스트)
│
├── infrastructure/             ← 외부 세계와의 I/O
│   ├── base.py                   BaseAdapter — 타임아웃·재시도·풀 수명
│   ├── stores.py                 Redis/Mongo/Kafka/REST 어댑터
│   ├── fake_data.py              ★ 실제 구현에서는 통째로 사라짐
│   ├── llm.py                    LLM 단일 경로 + 가드레일 + replay
│   └── delivery.py               파일 쓰기 / SMTP 발송
│
├── config/                     ← 설정을 "읽는 코드"
│   ├── loader.py                 3단 deep merge + 출처 추적
│   ├── env.py                    EnvConfig — 접속 정보·비밀값
│   └── registry.py               자동 스캔 + @register + 부팅 3중 검증
│
└── constants.py                  매직값 금지

config/                         ← 설정 "데이터" · GBM/FCT축 · 커밋됨
├── gbm/mx.json
└── factories/gumi/{common,mx}.json

.env                            ← 접속 정보 · 환경축 · 커밋 안 됨
.env.example                      템플릿 (커밋됨)
```

`src/config/`(코드)와 최상위 `config/`(JSON 데이터)는 이름이 같지만 다른 것입니다. 전자가 후자를 읽습니다.

### presentation과 infrastructure의 경계

가장 헷갈리는 지점이 **리포트 출력**입니다. "리포트를 만들어 파일로 저장하고 메일로 보내는 것"이 한 덩어리처럼 보이지만, **갈라집니다.**

| | 하는 일 | 계층 |
|---|---|---|
| `renderers.py` | `ReportSection` → md **문자열** | presentation |
| `templates/` | 어떤 양식으로 보일지 | presentation |
| `FileDelivery` | 그 문자열을 **디스크에 쓰기** | infrastructure |
| `MailDelivery` | 그 문자열을 **SMTP로 보내기** | infrastructure |

판별 기준은 하나입니다 — **"이걸 바꾸면 사용자가 보는 내용이 달라지나, 도착 경로만 달라지나?"**

- md → HTML: **내용**이 달라짐 → presentation
- 파일 → 메일: 같은 내용이 다른 곳으로 → infrastructure

`MailDelivery`는 리포트에 **무엇이 쓰여 있는지 전혀 모릅니다.** 완성된 문자열을 SMTP 소켓에 밀어넣을 뿐이라, 파일 쓰기와 본질적으로 같은 일입니다.

같은 이유로 **CLI와 스케줄러도 갈라집니다.** 둘 다 유스케이스를 호출하지만:

- **CLI** → presentation (사람이 명령하고 사람이 결과를 봄)
- **Scheduler** → infrastructure (시간 트리거, 사람 없음)

Hexagonal은 **방향축**(driving/driven), Clean은 **관심사축**(presentation/infrastructure)으로 자릅니다. 폴더는 1차원이라 하나를 골라야 하는데, 이 저장소는 관심사축을 택했습니다 — "presentation = 사람과 맞닿는 면"이 더 직관적인 기준이기 때문입니다.

### presentation은 출력, infrastructure는 입력? — 아닙니다

흔한 오해라 짚어둡니다. **둘 다 입력과 출력을 각각 갖습니다.**

|  | 들어옴 | 나감 |
|---|---|---|
| **presentation** (상대가 **사람**) | `cli.py` 인자 파싱 | `renderers.py` + `templates/` |
| **infrastructure** (상대가 **기계**) | `stores.py` 조회 | `delivery.py` 파일·SMTP |

가르는 건 방향이 아니라 **"상대가 누구냐"**입니다.

다만 이 프로젝트는 그렇게 **보이게** 생겼습니다 — presentation의 입력이 `--gbm mx` 정도로 작고, infrastructure의 출력이 파일 하나 메일 하나로 작기 때문입니다. 웹 앱이었으면 정반대로 느껴집니다(HTTP 요청 파싱·폼 검증으로 presentation 입력이 훨씬 무거워짐).

**그리고 축이 사실 셋입니다.** DB 조회는 데이터로는 입력이지만 Hexagonal에서는 `driven`입니다 — 데이터가 들어와도 **호출은 우리가 하기** 때문입니다.

| | 데이터 방향 | 호출 방향 | 상대 |
|---|---|---|---|
| CLI 인자 | 들어옴 | **driving** (밖이 우리를 부름) | 사람 |
| DB 조회 | 들어옴 | **driven** (우리가 밖을 부름) | 기계 |
| 리포트 렌더 | 나감 | driven | 사람 |
| 메일 발송 | 나감 | driven | 기계 |

데이터 방향 · 호출 방향 · 상대가 전부 다른 축이고, 폴더 구조는 그중 **상대**를 표현합니다.

| Clean 링 | 이 저장소 |
|---|---|
| Entities | `domain/models.py` |
| Use Cases | `application/` |
| Interface Adapters | `presentation/` (controller·presenter) + `infrastructure/` (gateway) |
| Frameworks & Drivers | `infrastructure/` 안의 motor·httpx·aiokafka 호출부 |

`domain/ports.py`가 이 셋을 잇는 계약입니다. `MetricPort`는 `infrastructure`가, `ReportRendererPort`는 `presentation`이 구현하고, `application`은 어느 쪽도 import하지 않습니다.

---

## 핵심 개념

### 1. config 3단 병합

뒤가 앞을 덮어씁니다.

```
config/gbm/{gbm}.json
  → config/factories/{factory}/common.json
    → config/factories/{factory}/{gbm}.json
```

`config show`가 값마다 **어느 계층에서 왔는지** 알려줍니다. 3단 merge에서 "이 값이 왜 이래?"는 반드시 나오는 질문이고, 이게 없으면 파일 셋을 눈으로 대조하게 됩니다.

```
subgraphs.kafka.lag.critical_lag       ← factory_gbm
subgraphs.kafka.lag.groups             ← factory_common
subgraphs.kafka.lag.warn_lag           ← gbm
```

**비밀값은 config에 넣지 않습니다.** JSON은 git에 커밋되므로 비밀번호·토큰은 `.env` → `EnvConfig`로만 옵니다. 자세한 경계는 [설정은 두 군데로 갈린다](#설정은-두-군데로-갈린다)를 보세요.

### 2. 등록명은 파일 경로에서 유도된다

`src/application/subgraphs/` 아래를 부팅 시 재귀 스캔해 import하면 데코레이터가 자기를 등록합니다. **이름을 주지 않으면 모듈 경로가 곧 이름**입니다.

```
subgraphs/kafka/lag.py  →  "kafka.lag"
```

그래서 config의 이름만 보면 파일 위치를 압니다. 다만 이건 경로 문자열이 아니라 **레지스트리 키**입니다 — 파일을 옮기거나 이름을 바꿀 때 `@register("kafka.lag")`로 고정하면 config를 건드리지 않아도 됩니다.

### 3. 부팅 시 3중 검증

하나라도 실패하면 프로세스가 뜨지 않고, **모든 문제를 모아서 한 번에** 보고합니다. 3단 merge 환경에서 하나씩 고치며 재시작하는 건 고통스럽기 때문입니다.

```
설정 검증에 실패했습니다 (4건):
  - config의 'subgraphs.alarm.latancy'이 레지스트리에 없습니다. 등록된 서브그래프: ...
  - 서브그래프 'alarm.trend'이 등록되었지만 어느 config에도 없습니다 (고아).
  - 'subgraphs.kafka.lag.warn_lag': Input should be a valid integer (입력: '다섯천')
  - 'subgraphs.line.equipment.nodes.process'가 가리키는 'nodes.does_not_exist'이 없습니다.
```

새벽 8시 배치가 아니라 **프로세스 시작 시점**에 터지는 게 요점입니다.

### 4. 서브그래프 4슬롯

```
validate_input ──→ process ──→ generate_output ──→ (ReportSection)
      │ 실패          │ 실패          │ 실패
      └──────────────┴──────────────┴──→ handle_error ──→ (degraded 섹션)
```

**한 서브그래프의 실패가 리포트 전체를 죽이지 않습니다.** 8시 리포트가 아예 안 오는 것보다 한 섹션이 비어서 오는 게 낫습니다.

슬롯 간 이동은 **전부 `Command`가 결정**합니다. 정적 엣지를 남기면 `Command(goto=handle_error)`가 그것을 대체하지 않고 *추가로* 동작해서, 실패 시 다음 슬롯과 `handle_error`가 같은 스텝에 함께 돌며 충돌합니다. 성공 경로까지 `Command`로 명시해야 하는 이유입니다.

### 5. 시간은 밖에서 주입한다

```python
class BaseContext(BaseModel):
    as_of: datetime      # 반드시 밖에서 주입
    gbm: str
    factory: str

class SnapshotContext(BaseContext): ...          # 현재 상태
class HistoricalContext(BaseContext):            # 구간
    start_dt: datetime
    end_dt: datetime
```

노드 안에서 `datetime.now()`를 부르면 재개(Durable Execution)·Time Travel·멱등성이 **모두** 깨집니다. 재개 시각이 달라져 앞뒤 노드가 서로 다른 시점을 보게 되기 때문입니다.

config에는 상대 시간으로 쓰고(`"window": "P7D"`), `validate_input`이 `as_of` 기준 절대 시각 쌍으로 바꿉니다. config는 읽기 쉽고 실행은 고정됩니다.

### 6. GBM/FCT별 분기는 config로만

코드 레벨 특수화(`register(gbm="mx", fct="GUMI", ...)`)는 두지 않습니다. 3단 merge가 이미 GBM/FCT 해석기인데 두 번째 해석기를 만들면, "지금 실제로 도는 구현이 뭔가"를 알기 위해 두 군데를 대조해야 합니다.

**상속은 코드에서, 선택은 config에서.** 실제로 [material/stock_gumi.py](src/application/subgraphs/material/stock_gumi.py)가 이렇게 동작합니다.

```python
# subgraphs/material/stock_gumi.py   → "material.stock_gumi"
@register(slot_only=True)          # ← config에 직접 등록되지 않는다는 선언
class GumiMaterialStock(MaterialStock):
    async def process(self, state): ...
```
```json
// config/factories/gumi/mx.json
{ "subgraphs": { "material.stock": { "nodes": { "process": "material.stock_gumi" } } } }
```

결과는 이렇습니다 — 같은 config 이름에 공장별로 다른 로직이 붙습니다.

```
mx/gumi  → 재고 1,263ea · 다음 입고까지 1시간 · 여유 +4.8시간     (구미 전용)
mx/asan  → 재고 1,263ea · 시간당 219ea 소비                      (기본)
```

**`slot_only=True`가 필요한 이유**: 이 클래스는 구미 config에서만 참조되므로, 표시가 없으면 다른 공장에서 "고아" 검증에 걸립니다. "독립 실행용이 아니라 슬롯 부품"이라는 선언입니다.

슬롯 override 대상은 두 종류를 받습니다.

| 대상 | 예 | 동작 |
|---|---|---|
| 공유 노드 부품 | `outputs.no_llm` | 함수를 원래 인스턴스에 바인딩 |
| 다른 서브그래프 | `material.stock_gumi` | **그 클래스로 인스턴스를 만들어** 슬롯을 가져옴 |

후자가 인스턴스를 따로 만드는 이유는, 메서드만 빌려오면 그 클래스의 헬퍼 메서드나 상수에 접근할 수 없기 때문입니다.

`python -m src registry`가 쓸 수 있는 이름을 전부 보여줍니다.

---

## 새 분석 추가하기

**파일 1개 + config 1줄**이면 끝납니다. 아래는 요약이고, 실제 데이터 조회부터 공장별 임계치까지 단계별로 따라 하려면 **[튜토리얼](docs/tutorial.md)**을 보세요.

### 1) 파일을 놓습니다

```python
# src/application/subgraphs/quality/defect.py   → 등록명 "quality.defect"
from src.application.subgraphs.base import BaseSubgraph, SubgraphConfig, SubgraphState
from src.config.registry import register
from src.domain.models import FetchSpec, Judgement, Metric, Severity, SnapshotContext


class DefectConfig(SubgraphConfig):
    threshold_ppm: int = 500          # 부팅 시 이 스키마로 검증된다


@register()
class Defect(BaseSubgraph):
    title = "불량률 점검"
    config_model = DefectConfig
    context_type = SnapshotContext    # 구간이면 HistoricalContext

    async def process(self, state: SubgraphState) -> dict:
        records = await self.deps.rest.fetch(state.scoped, FetchSpec(kind="defect"))
        metrics, judgements = [], []
        for rec in records:
            ppm = rec.record["ppm"]
            metrics.append(Metric(name=rec.metadata["line"], value=ppm, unit="ppm"))
            if ppm > self.config.threshold_ppm:
                judgements.append(Judgement(
                    subject=f"{rec.metadata['line']} 불량률 초과",
                    severity=Severity.WARNING,
                    reasoning=f"{ppm}ppm이 기준 {self.config.threshold_ppm}ppm을 넘었습니다",
                    evidence=[rec.id],       # 실제 Record id여야 한다
                ))
        return {"records": records, "metrics": metrics, "judgements": judgements}
```

보통 **`process`만 구현**하면 됩니다. `validate_input`/`generate_output`/`handle_error`는 `BaseSubgraph`가 처리합니다.

### 2) config에 씁니다

```json
{ "subgraphs": { "quality.defect": { "enabled": true, "threshold_ppm": 300 } } }
```

이게 전부입니다. 리포트 섹션도 자동으로 생기고, 템플릿은 고칠 게 없습니다.

### 슬롯만 갈아끼우기

특정 GBM/FCT에서 한 슬롯만 다르게 하고 싶으면:

```json
{ "subgraphs": { "kpi.check": { "nodes": { "output": "outputs.no_llm" } } } }
```

이 한 줄로 `kpi.check`의 LLM 서술만 꺼집니다(지표는 그대로). 슬롯 이름은 `validate` / `process` / `output` / `error`입니다.

---

## 설정은 두 군데로 갈린다

`config/*.json`과 `.env`는 **자르는 축이 다릅니다.**

| | `config/*.json` | `.env` |
|---|---|---|
| 무엇으로 갈리나 | **GBM/FCT** (사업부·공장) | **배포 환경** (dev/stg/prod) |
| git 커밋 | ✓ | ✗ (`.gitignore`) |
| 담는 것 | 임계치, 기능 on/off, 템플릿, 수신자 목록, 스케줄 | 접속 주소, 계정, 비밀번호, 토큰 |
| 읽는 곳 | `config/loader.py` (3단 merge) | `config/env.py` (`EnvConfig`) |

핵심은 **같은 `mx/gumi`라도 dev 서버와 prod 서버는 다른 Mongo를 본다**는 점입니다. 그건 3단 merge로 표현할 수 없으므로 `.env`가 그 축을 담당합니다.

애매하면 두 가지를 물어보면 됩니다:

1. **git에 커밋해도 되는가?** → 아니면 `.env`
2. **GBM/FCT가 아니라 환경에 따라 바뀌는가?** → 그러면 `.env`

### 갈라지는 실제 사례

같은 기능인데 설정이 양쪽으로 나뉘는 경우가 있습니다.

| | config JSON | `.env` |
|---|---|---|
| **LLM** | `provider`, `model`, `temperature`, `seed`<br><sub>공장마다 다른 모델을 쓸 수 있음</sub> | `LLM_BASE_URL`, `LLM_API_KEY`<br><sub>게이트웨이 주소는 환경별</sub> |
| **메일** | `recipients`, `subject_prefix`<br><sub>수신자는 공장별</sub> | `SMTP_HOST`, `SMTP_USER`, `SMTP_PWD`<br><sub>메일 서버는 환경별</sub> |
| **저장소** | `stores.timeout_sec`<br><sub>정책</sub> | `MONGODB_URI`, `REDIS_URL`<br><sub>주소</sub> |

두 설정은 `build_dependencies(cfg, env)`에서 만납니다. **어느 노드도 이 둘을 보지 않습니다** — 조립 시점에 필요한 값만 뽑아 넘깁니다.

### 시작하기

| OS / 셸 | 명령 |
|---|---|
| Linux / macOS | `cp .env.example .env` |
| Windows PowerShell | `Copy-Item .env.example .env` |
| Windows cmd | `copy .env.example .env` |

[.env.example](.env.example)에 전체 목록과 설명이 있습니다. 주요 항목:

```bash
DEPLOY_GBM=mx                 # CLI의 --gbm이 우선
DEPLOY_FACTORY=gumi

REDIS_URL=redis://redis.internal:6379/0
MONGODB_URI=mongodb://mongo.internal:27017
MONGODB_DB=mes
CHECKPOINT_DB=langgraph       # 체크포인터용 별도 DB
KAFKA_BOOTSTRAP_SERVERS=kafka1.internal:9092
REST_BASE_URL=https://mes-api.internal

LLM_BASE_URL=http://llm-gateway.internal/v1
LLM_API_KEY=

SMTP_HOST=smtp.internal
```

**모든 항목에 기본값이 있어 `.env` 없이도 뜹니다.** 지금은 전부 스텁이라 접속 정보가 비어 있어도 동작하고, 실제 연결로 전환하면 빈 값은 어댑터가 연결 시도 시 실패합니다.

---

## 리포트 템플릿

`src/presentation/templates/`에 있고, **config 한 줄로 교체**합니다.

```json
{ "report": { "template": "report_brief.md" } }
```

`application` 계층은 템플릿의 존재를 모릅니다. 노드는 `ReportSection` **객체**를 만들 뿐이고, 어떤 양식으로 찍을지는 presenter의 몫입니다. GBM/FCT마다 다른 양식을 쓰려면 3단 merge의 아래 계층에서 이 키만 덮어쓰면 됩니다.

### 문법

```markdown
<!-- block: document -->
# 운영 상태 리포트 — ${gbm} / ${factory}

- 기준 시각: `${as_of}`
${overall}${sections}

<!-- block: section -->

## ${mark} ${title}${degraded_mark}

${narrative}

${metrics}
```

- `<!-- block: 이름 -->` — 블록 구분. **이 줄만 제거되고 본문 여백은 보존**됩니다
- `${변수}` — 치환 (파이썬 `string.Template`). 없는 변수는 빈 문자열
- 각 블록이 자기 앞뒤 빈 줄을 들고 있습니다 (렌더러가 그냥 이어붙이므로). 비어버린 블록이 남긴 여백은 마지막에 자동 정리됩니다

### 블록과 변수

| 블록 | 필수 | 쓸 수 있는 변수 |
|---|---|---|
| `document` | ✓ | `gbm` `factory` `as_of` `section_count` `overall` `sections` |
| `overall` | | `critical` `warning` `normal` `degraded` `narrative` `top_issues` |
| `section` | | `key` `title` `mark` `severity` `degraded_mark` `narrative` `narrative_inline` `metrics` `judgements` |
| `judgements` | | `items` |
| `judgement_item` | | `subject` `severity` `confidence` `reasoning` `evidence` |

**블록을 정의하지 않으면 그 부분이 통째로 생략됩니다.** `report_brief.md`가 `judgements`/`judgement_item`을 비워두어 판정 근거를 빼는 방식입니다.

`section`과 `judgement_item`은 **반복 블록**이라 렌더러가 항목마다 한 번씩 채웁니다. 섹션은 심각도 내림차순, 같으면 이름순으로 정렬됩니다.

### 새 양식 추가

`src/presentation/templates/`에 파일을 놓고 config에서 이름을 부르면 끝입니다. **템플릿 파일이 없으면 부팅 시 터집니다** — 8시 배치가 아니라요.

```
✗ 템플릿 파일이 없습니다: .../src/presentation/templates/report_typo.md
```

HTML 메일처럼 md가 아닌 형식이 필요하면 `ReportRendererPort`를 구현한 어댑터를 하나 더 만들어 `Dependencies`에서 바꿔 끼웁니다. 노드는 그대로입니다.

---

## LLM 규약

LLM은 **취합 단계와 각 서브그래프 양쪽**에서 씁니다. 대신 디버깅 가능성이 전제 조건이라 네 가지 장치가 들어갑니다.

### 숫자는 LLM이 다시 쓰지 않는다

"p95가 412ms"를 문장으로 옮기게 하면 언젠가 421ms로 씁니다. **숫자는 템플릿이 State에서 직접 렌더링**하고 LLM은 서술만 담당합니다.

### 판정에 근거를 강제한다

```python
class Judgement(BaseModel):
    subject: str
    severity: Severity
    reasoning: str
    evidence: list[str]      # 실제 입력 Record의 id
    confidence: float
```

`evidence`가 실제 id를 가리키므로 리포트의 어떤 문장이든 원본까지 역추적됩니다.

### 근거를 코드로 검증한다 (환각 가드레일)

LLM이 든 `evidence` id가 실제 입력에 없으면 **그 판정을 폐기**합니다. LLM 없이 도는 결정론적 검증이라 항상 켜 둡니다.

```
⚠ 가드레일: kpi.check: 근거 ['kpi:가동률::hallucinated']가 입력에 없어 판정을 폐기했습니다
```

### 프롬프트·응답 원문을 남긴다

`LLMTrace`가 **변수 치환이 끝난 최종 프롬프트**와 raw 응답, model, temperature를 담아 State에 쌓입니다. 체크포인터에 자동 저장되므로 별도 로깅 인프라가 없습니다.

### 전체 요약의 재료

구조화된 `judgements`와 **사전 계산된 집계치**를 줍니다. 세부 요약 문장을 다시 요약하면 정보가 2단으로 손실되고, 무엇보다 "critical 몇 건"을 정확히 세지 못합니다. 개수는 코드가 세고 LLM은 해석만 합니다.

---

## LLM 연결과 모델 교체

**실제 LLM과 통신하는 코드는 `ChatModelAdapter` 한 곳에 있습니다.** [src/infrastructure/llm.py](src/infrastructure/llm.py)의 메서드 셋이 전부입니다:

| 메서드 | 하는 일 |
|---|---|
| `_ensure_client()` | `init_chat_model()`로 클라이언트 생성 (지연 로드) |
| `_complete()` | 서술 생성 — `ainvoke()` |
| `_judge_raw()` | 판정 생성 — `with_structured_output()` |

나머지(프롬프트 기록, replay, 환각 가드레일)는 `BaseLLMAdapter`에 있고 가짜/실제가 **공유**합니다. 그래서 fake로 개발하다 실제로 바꿔도 관측·검증 동작이 그대로입니다.

```
BaseLLMAdapter          ← 기록, replay, 가드레일 (공통)
├── FakeLLMAdapter      ← 규칙 기반. 외부 통신 없음. 기본값
└── ChatModelAdapter    ← ★ 실제 LLM과 통신하는 유일한 지점
```

### 실제 LLM으로 전환

config 한 줄입니다.

```json
{ "llm": { "adapter": "chat_model" } }
```

| 값 | 동작 |
|---|---|
| `"fake"` (기본) | 규칙 기반 가짜 응답. LLM 없이 개발·테스트 |
| `"chat_model"` | 실제 LLM 호출 |

`langchain-openai` 같은 공급자 패키지는 **`chat_model`을 고를 때만** 로드됩니다. 알 수 없는 값을 쓰면 부팅 시 막힙니다.

```
ValueError: 알 수 없는 llm.adapter 'gpt'. 가능: fake, chat_model
```

### 모델 바꾸기

config의 `llm.model` 한 줄입니다.

```json
{ "llm": { "adapter": "chat_model", "model": "gpt-4o-mini", "temperature": 0.0 } }
```

**공장마다 다른 모델**도 3단 merge로 됩니다. 아산 공장을 추가하고 거기만 더 큰 모델을 쓰려면 `config/factories/asan/common.json`을 만들어:

```json
{ "llm": { "model": "gpt-4o", "temperature": 0.2 } }
```

```
mx/gumi  → model=gpt-4o-mini  temp=0.0   (llm.model ← gbm)
mx/asan  → model=gpt-4o       temp=0.2   (llm.model ← factory_common)
```

`config show`가 어느 계층에서 온 값인지 알려줍니다.

> 이 저장소에는 `config/factories/gumi/`만 있습니다. 위는 새 공장을 추가했을 때의 모습입니다. **없는 factory를 지정해도 에러가 나지 않고** gbm 계층만 적용되니, `config show`로 어느 계층 파일이 실제로 읽혔는지(`○`/`×`) 확인하세요.

### 공급자 바꾸기

| 어디서 | 무엇 | 왜 |
|---|---|---|
| config JSON | `provider`, `model`, `temperature` | GBM/FCT마다 다를 수 있음 |
| `.env` | `LLM_BASE_URL`, `LLM_API_KEY` | 환경(dev/prod)마다 다름 |

사내 게이트웨이가 OpenAI 호환이면 **`.env`의 `LLM_BASE_URL` 교체만으로** 끝납니다. `provider`는 `PROVIDER_ALIAS`를 통해 `init_chat_model`이 아는 이름으로 매핑되며, `openai_compatible`은 `openai`가 됩니다.

### 서브그래프별로 다른 모델

기본은 그래프 전체가 LLM 하나를 공유하지만, **특정 서브그래프만 다른 모델**을 쓸 수 있습니다. 해당 블록에 `llm`을 넣으면 됩니다.

> 실제 예시가 [config/gbm/mx.json](config/gbm/mx.json)의 `kpi.check`에 들어 있습니다 — LLM 판정을 쓰는 서브그래프라 모델만 따로 지정했습니다.

```json
{
  "llm": { "adapter": "chat_model", "model": "gpt-4o-mini" },
  "subgraphs": {
    "kafka.lag":  { "enabled": true },
    "kpi.check":  { "enabled": true,
                    "llm": { "model": "gpt-4o" } }
  }
}
```

기본 설정 위에 **deep merge**되므로 바꿀 키만 적으면 됩니다 — 위에서 `kpi.check`는 `adapter`를 안 적었지만 `chat_model`을 그대로 물려받습니다.

실제로 어떤 모델이 쓰였는지는 `LLMTrace`에 남습니다. 기본 config로 돌리면 이렇게 나옵니다.

```
aggregate              fake-local
alarm.trend            fake-local
health.connectivity    fake-local
kafka.lag              fake-local
kpi.check              fake-judge    ←config의 llm override
line.equipment         fake-local
material.stock         fake-local
```

`config show`의 출처 추적에도 잡힙니다.

```
subgraphs.kpi.check.llm.model        ← gbm
```

**기본만 fake로 두고 한 서브그래프만 실제 LLM**을 붙이는 것도 됩니다 — 새 분석의 프롬프트를 다듬을 때 유용합니다.

```json
{ "llm": { "adapter": "fake" },
  "subgraphs": { "kpi.check": { "llm": { "adapter": "chat_model", "model": "gpt-4o" } } } }
```

override가 없는 서브그래프는 **기본 인스턴스를 그대로 재사용**합니다(불필요한 클라이언트를 만들지 않기 위해). 취합 노드는 항상 기본 LLM을 씁니다.

3단 merge도 그대로 적용되므로, 특정 공장에서만 특정 서브그래프의 모델을 올리는 것도 config로 됩니다.

> **가드레일 경고는 State로 모입니다.** LLM이 여러 개가 되면 어댑터에 쌓아둔 기록을 한곳에서 읽을 수 없으므로, `guardrail_drops`가 `traces`처럼 State에 누적됩니다. 체크포인터에도 함께 저장됩니다.

---

## 데이터 흐름

```
CLI ─→ BaseContext(as_of 확정)
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

---

## CLI

| 명령 | 설명 |
|---|---|
| `run --gbm --factory [--as-of] [--stream] [--quiet]` | 리포트 생성 |
| `config show --gbm --factory` | 병합 결과 + 각 값의 출처 |
| `registry` | 등록된 서브그래프 목록 |

`--gbm`/`--factory`를 생략하면 `.env`의 `DEPLOY_GBM`/`DEPLOY_FACTORY`를 씁니다.

---

## 실제 연결로 전환

### 데이터 소스

`src/infrastructure/stores.py`의 각 어댑터에 실제 호출이 주석으로 있습니다. `fake_data` 호출을 그것으로 바꾸고 `fake_data.py`를 지우면 됩니다.

```python
async def _do():
    # 지금:
    return fake_data.redis_production(ctx.as_of)
    # 실제:
    # keys = await self._client.keys(pattern)
    # return await self._client.mget(keys)
```

`_to_domain` 호출 위치와 `BaseAdapter._call`(타임아웃·재시도)은 그대로 둡니다.

### LLM

**코드를 고칠 필요가 없습니다.** 실제 어댑터가 이미 구현되어 있고, config에서 고릅니다.

```json
{ "llm": { "adapter": "chat_model", "provider": "openai_compatible", "model": "gpt-4o-mini" } }
```
```bash
# .env
LLM_BASE_URL=http://llm-gateway.internal/v1
LLM_API_KEY=...
```

자세한 내용은 [LLM 연결과 모델 교체](#llm-연결과-모델-교체)를 보세요.

### 체크포인터

`src/application/graph/builder.py` 끝에 주석이 있습니다.

```python
# from langgraph.checkpoint.mongodb import MongoDBSaver
# return graph.compile(checkpointer=MongoDBSaver(client, db_name=...))
```

이걸 붙이면 Durable Execution과 Time Travel이 따라옵니다(별도 구현 없음).

### 렌더러

지금은 표준 라이브러리(`string.Template`)로 템플릿을 채웁니다 — 이 환경에 pip/uv가 없어 Jinja2를 설치할 수 없었습니다. 반복문·조건문이 필요해지면 Jinja2 어댑터를 추가하고 `Dependencies`에서 바꿔 끼우면 됩니다. `ReportRendererPort` 뒤에 있으므로 노드와 템플릿 사용법(config의 `report.template`)은 그대로입니다.

---

## 아직 없는 것

| | 상태 | 비고 |
|---|---|---|
| 어댑터 테스트 | **의도적으로 없음** | 나머지 4개 층은 [tests/](tests/README.md)에 있습니다. 어댑터만 안 만들기로 한 이유는 [설계 문서 §11](docs/superpowers/specs/2026-08-13-langgraph-report-template-design.md) |
| 체크포인터 | 미연결 | Time Travel·재개 미동작 |
| 상주 스케줄러 | 미구현 | CLI만 있음. 설계상 `infrastructure/scheduler.py` |
| `schedule` config | **읽는 코드 없음** | `config/gbm/mx.json`에 `cron`/`timezone` 블록이 있지만 현재 무시됩니다. 스케줄러를 붙일 때 사용 |
| 중복 실행 락 | 미구현 | Mongo `{gbm,factory,as_of}` 유니크 인덱스 예정 |
| replay 모드 | 어댑터만 준비 | `--replay` 플래그 미연결 |

`fake_data.py`는 `as_of`로 난수 시드를 고정하므로 **같은 `as_of`는 항상 같은 데이터**를 돌려줍니다. 실제 연결 전에도 멱등성을 확인할 수 있습니다.

---

## 요구 환경

- Python 3.12
- langgraph 1.2.x, langchain 1.3.x, pydantic 2.x, pydantic-settings 2.x
