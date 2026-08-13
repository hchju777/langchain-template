# 튜토리얼 — 새 분석을 리포트에 추가하기

Redis에서 데이터를 가져와 로직을 돌리고, 그 결과가 리포트 섹션으로 나오기까지의 전 과정을 따라 합니다.

**만들 것**: 라인별 자재 재고를 읽어 "몇 시간 뒤에 떨어지는가"를 계산하고, 임계치보다 짧으면 경고하는 분석.

완성본은 [src/application/subgraphs/material/stock.py](../src/application/subgraphs/material/stock.py)에 있습니다 — 이 문서를 따라 만든 결과물이고 실제로 돌아갑니다. 막히면 열어보세요.

**걸리는 시간**: 15분. 작성하는 코드는 약 60줄이고, 그중 절반이 주석입니다.

> ### ⚠ 먼저 읽으세요 — 이 예시는 이미 적용되어 있습니다
>
> `material.stock`은 저장소에 **이미 만들어져 config에도 등록된 상태**입니다. 그래서 문서를 그대로 따라 하면 "이미 있다"거나 "아무 일도 안 일어난다"가 됩니다.
>
> 직접 손으로 만들어보려면 **먼저 지우고 시작하세요.**
>
> ```bash
> # 1) 서브그래프 파일 (stock_gumi.py는 5단계 이후에 나오니 함께 지웁니다)
> rm -rf src/application/subgraphs/material
>
> # 2) config/gbm/mx.json 에서 "material.stock" 블록 삭제
> # 3) config/factories/gumi/common.json 에서 "material.stock" 블록 삭제
> # 4) config/factories/gumi/mx.json 에서 "material.stock" 블록 삭제
> # 5) src/infrastructure/stores.py 의 table 에서 "material_stock" 줄 삭제
> # 6) src/infrastructure/fake_data.py 의 redis_material_stock 함수 삭제
> ```
>
> 지우기 싫으면 **읽기만 해도 됩니다.** 각 단계의 실제 출력을 그대로 실었으니 흐름은 따라올 수 있고, 완성본 파일을 열어 대조하면 됩니다.

> 시작 전에 [README](../README.md)의 "구조"와 "핵심 개념"을 훑어두면 이해가 빠릅니다. 안 봐도 따라올 수는 있습니다.

### 준비

가상환경을 활성화해두면 이 문서의 명령이 그대로 동작합니다.

| OS / 셸 | 명령 |
|---|---|
| Linux / macOS | `source .venv/bin/activate` |
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows cmd | `.venv\Scripts\activate.bat` |

활성화하지 않으려면 `python` 자리에 `.venv/bin/python`(Linux·macOS) 또는 `.venv\Scripts\python.exe`(Windows)를 넣으세요.

> PowerShell에서 실행 정책 오류가 나면: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`
>
> 구형 `cmd`에서 이모지가 깨지면: `chcp 65001`

---

## 전체 그림

새 분석 하나를 추가할 때 손대는 곳은 **두 군데**입니다.

```
① 파이썬 파일 1개                    ② config 1블록
   subgraphs/material/stock.py         config/gbm/mx.json
        │                                   │
        └──── 부팅 시 자동 스캔 ────→ 레지스트리 ←── 이름으로 참조
                                            │
                                    검증 통과하면 그래프에 조립
                                            │
                          ┌─────────────────┴─────────────────┐
                          ↓                                   ↓
                   병렬 실행 → ReportSection → 취합 → md 리포트
```

데이터를 새로 가져와야 하면 여기에 **③ 어댑터에 조회 추가**가 붙습니다. 기존 데이터를 쓴다면 ①②만 하면 됩니다.

---

## 1단계 — 어떤 데이터가 필요한지 정한다

우리에게 필요한 건 라인별 자재 재고와 시간당 소비량입니다. 지금 Redis 어댑터가 무엇을 줄 수 있는지 봅니다.

[src/infrastructure/stores.py](../src/infrastructure/stores.py)의 `RedisAdapter.fetch`:

```python
table = {
    "production": fake_data.redis_production,
    "line_info": fake_data.redis_line_info,
    "equipment_status": fake_data.redis_equipment_status,
}
```

자재 재고가 없으니 추가해야 합니다. **기존 `kind`로 충분하다면 이 단계를 건너뛰고 2단계로 가세요.**

### 데이터 형태

모든 수집 데이터는 `Record` 한 가지 형태로 들어옵니다.

```python
class Record(BaseModel):
    id: str                      # 판정의 근거로 인용될 식별자
    metadata: dict[str, Any]     # 식별·분류 정보 (라인, 자재명…)
    record: dict[str, Any]       # 실제 값 (재고량, 소비량…)
```

`id`가 중요합니다. 나중에 판정을 내릴 때 **이 id를 근거로 인용**하고, 그래야 리포트의 문장에서 원본 데이터까지 역추적됩니다.

### 스텁 데이터 추가

지금은 DB 연결이 스텁이므로 [fake_data.py](../src/infrastructure/fake_data.py)에 넣습니다.

```python
def redis_material_stock(as_of: datetime) -> list[dict]:
    rng = _rng(as_of, "material")          # as_of로 시드 고정 → 재실행해도 같은 값
    rows = []
    for line in LINES:
        for material in ("MAT-A", "MAT-B"):
            rows.append({
                "id": f"stock:{line}:{material}",
                "metadata": {"line": line, "material": material},
                "record": {
                    "stock_qty": rng.randint(120, 5_000),
                    "hourly_consumption": rng.randint(80, 400),
                },
            })
    return rows
```

그리고 어댑터의 `table`에 한 줄 추가합니다.

```python
table = {
    ...
    "material_stock": fake_data.redis_material_stock,
}
```

> **실제 Redis를 쓴다면** `fake_data` 호출 대신 그 자리에 실제 조회를 씁니다. 나머지 단계는 완전히 동일합니다.
>
> ```python
> async def _do():
>     keys = await self._client.keys("stock:*")
>     vals = await self._client.mget(keys)
>     return [self._parse(k, v) for k, v in zip(keys, vals)]
> ```
>
> 어댑터가 원본을 `Record`로 바꾸는 일(`_to_domain`)은 **어댑터 안에서** 끝나야 합니다. 서브그래프는 Redis 키가 어떻게 생겼는지 몰라야 합니다.

---

## 2단계 — 서브그래프 파일을 만든다

**파일을 어디에 놓느냐가 곧 이름입니다.**

```
src/application/subgraphs/material/stock.py   →   등록명 "material.stock"
```

`material/` 폴더와 그 안의 `__init__.py`를 만들고 `stock.py`를 씁니다.

### 2-1. config 스키마

이 분석이 config에서 받을 값을 Pydantic 모델로 선언합니다.

```python
from src.application.subgraphs.base import BaseSubgraph, SubgraphConfig, SubgraphState
from src.config.registry import register
from src.domain.models import FetchSpec, Judgement, Metric, Severity, SnapshotContext


class MaterialStockConfig(SubgraphConfig):
    warn_hours: float = 8.0
    critical_hours: float = 2.0
```

`SubgraphConfig`를 상속하면 `enabled`, `window`, `cache_ttl`이 딸려옵니다.

이 스키마 덕분에 **config 오타와 타입 오류가 부팅 시점에 잡힙니다.** 새벽 배치가 아니라요. dict로 받았다면 `config["warn_hourss"]`가 그 코드 경로를 지나갈 때야 터졌을 겁니다.

키 오타까지 잡히는 건 `SubgraphConfig`가 `extra="forbid"`를 쓰기 때문입니다. 이게 없으면 모르는 키가 조용히 무시되고 기본값으로 돕니다.

### 2-2. 클래스 뼈대

```python
@register()
class MaterialStock(BaseSubgraph):
    title = "자재 소진 예상"
    config_model = MaterialStockConfig
    context_type = SnapshotContext
```

| 항목 | 의미 |
|---|---|
| `@register()` | 인자 없이 쓰면 **파일 경로가 곧 등록명**. 파일을 옮겨도 config를 안 고치고 싶으면 `@register("material.stock")` |
| `title` | 리포트 섹션 제목 |
| `config_model` | 위에서 만든 스키마 |
| `context_type` | **현재 상태**를 보면 `SnapshotContext`, **구간**을 보면 `HistoricalContext` |

`context_type`이 시간 계약을 정합니다. `HistoricalContext`를 고르면 config에 `window`가 **필수**가 되고, `state.scoped`에 `start_dt`/`end_dt`가 채워집니다. 우리는 지금 재고를 보는 거라 스냅샷입니다.

### 2-3. process 구현

**보통 이 메서드 하나만 쓰면 됩니다.** 나머지 세 슬롯(`validate_input`, `generate_output`, `handle_error`)은 `BaseSubgraph`가 처리합니다.

```python
    async def process(self, state: SubgraphState) -> dict:
        cfg: MaterialStockConfig = self.config

        # ① 데이터를 가져온다
        records = await self.deps.redis.fetch(
            state.scoped, FetchSpec(kind="material_stock")
        )

        # ② 로직을 돌린다
        metrics, judgements = [], []
        for rec in records:
            line = rec.metadata["line"]
            material = rec.metadata["material"]
            stock = rec.record["stock_qty"]
            rate = rec.record["hourly_consumption"]
            hours_left = stock / rate if rate else float("inf")

            # ③ 숫자는 metrics에
            metrics.append(Metric(
                name=f"{line} {material}",
                value=round(hours_left, 1),
                unit="시간",
                note=f"재고 {stock:,}ea · 시간당 {rate}ea 소비",
            ))

            # ④ 판정에는 근거를 단다
            if hours_left <= cfg.critical_hours:
                severity = Severity.CRITICAL
            elif hours_left <= cfg.warn_hours:
                severity = Severity.WARNING
            else:
                continue

            judgements.append(Judgement(
                subject=f"{line} {material} 자재 부족",
                severity=severity,
                reasoning=(
                    f"재고 {stock:,}ea를 시간당 {rate}ea로 소비하면 "
                    f"{hours_left:.1f}시간 뒤 소진됩니다"
                ),
                evidence=[rec.id],
            ))

        return {"records": records, "metrics": metrics, "judgements": judgements}
```

여기서 지켜야 할 규약이 넷입니다.

**① 저장소 접근은 포트를 거칩니다.** `self.deps.redis`는 `SnapshotPort`를 구현한 어댑터고, 서브그래프는 Redis 키가 어떻게 생겼는지 모릅니다. 그래서 쿼리가 바뀌어도 이 코드는 그대로입니다.

> 다만 지금은 **어느 어댑터를 쓸지 config로 고를 수 없습니다.** `build_dependencies`가 하드코딩하고 `Dependencies.redis`처럼 필드명도 기술 이름입니다. 설계 문서 §5의 `ports` 바인딩은 미구현입니다.

**② 규칙으로 쓸 수 있으면 코드로 씁니다.** "재고 ÷ 소비량"과 임계치 비교는 LLM에게 시킬 일이 아닙니다. 느리고 비싸고 가끔 틀립니다.

**③ 숫자는 `metrics`에 담습니다.** 템플릿이 이 값을 직접 렌더링하고, LLM은 서술만 씁니다. LLM에게 "5.8시간"을 문장으로 옮기게 하면 언젠가 5.9시간으로 씁니다.

**④ `evidence`에는 실제 `Record.id`를 넣습니다.** 이게 리포트 문장 → 원본 데이터 역추적의 고리이고, LLM이 판정할 때는 이 id 대조가 환각 가드레일로 작동합니다.

`process`는 `records` / `metrics` / `judgements` 세 키를 담은 dict를 돌려주면 됩니다.

---

## 3단계 — config에 등록한다

여기서 한번 **일부러 실행해봅니다.** 아직 config에 안 넣었는데도 파일은 만들었으니까요.

```bash
python -m src run --gbm mx --factory gumi
```

```
✗ 설정 검증에 실패했습니다 (1건):
  - 서브그래프 'material.stock'이 등록되었지만 어느 config에도 없습니다 (고아).
    config에 추가하거나 파일을 지우세요.
```

**부팅이 멈췄습니다.** 등록은 됐는데 아무 config도 이 분석을 언급하지 않으니, 켤 생각이었는지 지울 생각이었는지 알 수 없다는 뜻입니다.

[config/gbm/mx.json](../config/gbm/mx.json)의 `subgraphs`에 추가합니다.

```json
{
  "subgraphs": {
    "material.stock": {
      "enabled": true,
      "warn_hours": 8.0,
      "critical_hours": 2.0
    }
  }
}
```

---

## 4단계 — 실행한다

```bash
python -m src registry
```

```
등록된 서브그래프 6건 (이름은 파일 경로에서 유도됨)

  alarm.trend              알람 추세 (scen_id별)     ← alarm.trend.py
  health.connectivity      연결 상태 점검             ← health.connectivity.py
  kafka.lag                Kafka Lag 상태         ← kafka.lag.py
  kpi.check                KPI 점검               ← kpi.check.py
  line.equipment           라인별 장비 상태            ← line.equipment.py
  material.stock           자재 소진 예상             ← material.stock.py
```

```bash
python -m src run --gbm mx --factory gumi --as-of 2026-08-13T08:00
```

리포트에 섹션이 생겼습니다.

```markdown
## 🟡 자재 소진 예상

L1 MAT-A: 5.8시간 재고 1,263ea · 시간당 219ea 소비 항목이 두드러집니다. 함께 …

| 지표 | 값 | 비고 |
| --- | ---: | --- |
| L1 MAT-A | 5.8 시간 | 재고 1,263ea · 시간당 219ea 소비 |
| L1 MAT-B | 5.0 시간 | 재고 1,829ea · 시간당 363ea 소비 |
| L2 MAT-A | 2.7 시간 | 재고 1,024ea · 시간당 380ea 소비 |
| L2 MAT-B | 27.4 시간 | 재고 4,212ea · 시간당 154ea 소비 |
| L3 MAT-A | 11.4 시간 | 재고 3,529ea · 시간당 309ea 소비 |
| L3 MAT-B | 19.1 시간 | 재고 3,558ea · 시간당 186ea 소비 |

<details><summary>판정 근거</summary>

- **L1 MAT-A 자재 부족** (경고, 확신도 100%)
  - 재고 1,263ea를 시간당 219ea로 소비하면 5.8시간 뒤 소진됩니다
  - 근거: `stock:L1:MAT-A`
…
</details>
```

**템플릿을 한 글자도 고치지 않았습니다.** 섹션 제목, 표, 판정 근거, 심각도 정렬, 전체 요약 반영이 전부 자동입니다.

---

## 5단계 — 공장마다 다른 임계치

구미 공장은 자재 입고 리드타임이 길어서 더 일찍 경고받고 싶다고 합시다. **코드를 건드리지 않습니다.**

[config/factories/gumi/common.json](../config/factories/gumi/common.json):

```json
{
  "subgraphs": {
    "material.stock": { "warn_hours": 12.0 }
  }
}
```

3단 병합에서 `factory_common`이 `gbm`을 덮어씁니다. 확인:

```bash
python -m src config show --gbm mx --factory gumi
```

```
subgraphs.material.stock.critical_hours     ← gbm
subgraphs.material.stock.enabled            ← gbm
subgraphs.material.stock.warn_hours         ← factory_common     ← 덮어써짐
```

경고가 3건에서 4건으로 늘었습니다. 다른 공장은 그대로 8시간을 씁니다.

**끄고 싶으면** `"enabled": false` 한 줄이면 됩니다. 리포트에서 섹션이 사라지고, 템플릿은 역시 안 고칩니다.

---

## 흔한 실수와 그때 나오는 메시지

앞의 둘(config 문제)은 **부팅 시점**에 잡히고, 여러 개면 **한 번에 모아서** 보고합니다. 뒤의 둘(코드 문제)은 부팅 검증이 볼 수 없어 **실행 중 부분 실패**로 나타납니다.

### config 이름에 오타

```json
{ "subgraphs": { "material.stok": { "enabled": true } } }
```

```
✗ 설정 검증에 실패했습니다 (2건):
  - config의 'subgraphs.material.stok'이 레지스트리에 없습니다.
    등록된 서브그래프: alarm.trend, health.connectivity, kafka.lag, kpi.check,
    line.equipment, material.stock
  - 서브그래프 'material.stock'이 등록되었지만 어느 config에도 없습니다 (고아).
```

오타 하나가 **두 방향으로** 잡힙니다 — 없는 이름을 불렀고, 있는 이름이 안 불렸습니다.

### 타입 오류

```json
{ "subgraphs": { "material.stock": { "warn_hours": "여덟시간" } } }
```

```
✗ 설정 검증에 실패했습니다 (1건):
  - 'subgraphs.material.stock.warn_hours': Input should be a valid number,
    unable to parse string as a number (입력: '여덟시간')
```

### 설정 키 오타

```json
{ "subgraphs": { "material.stock": { "warn_hourss": 12.0 } } }
```

```
✗ 설정 검증에 실패했습니다 (1건):
  - 'subgraphs.material.stock.warn_hourss': Extra inputs are not permitted (입력: 12.0)
```

`SubgraphConfig`의 `extra="forbid"` 덕분입니다. 이게 없으면 오타 난 키가 **조용히 무시되고** 원래 값은 기본값으로 돌아, 왜 임계치가 안 먹는지 한참 헤매게 됩니다.

### 구간 분석인데 window를 안 줌

`context_type = HistoricalContext`로 바꾸고 config에 `window`를 안 넣으면, 부팅이 아니라 **실행 중에** 그 서브그래프만 실패합니다.

```
⚠ 부분 실패: material.stock.validate — ValueError: material.stock는 구간 분석이므로
   config에 window가 필요합니다
```

리포트는 정상적으로 나오고, 그 섹션만 "부분 실패"로 표시됩니다. **한 분석의 실패가 나머지를 죽이지 않습니다** — 8시 리포트가 아예 안 오는 것보다 낫기 때문입니다.

### 어댑터에 없는 kind

```python
FetchSpec(kind="material_stok")     # 오타
```

```
⚠ 부분 실패: material.stock.process — AdapterError: redis 호출 실패:
   "redis: 알 수 없는 kind 'material_stok'"
```

이건 config가 아니라 코드라서 부팅 검증이 못 잡습니다. 대신 `process` 슬롯에서 격리됩니다.

---

## 더 해볼 것

### 구간 분석으로 만들기

"지난 24시간 소비 추세"처럼 기간을 보려면 두 가지만 바꿉니다.

```python
from src.domain.models import HistoricalContext   # ← import 추가를 잊지 마세요

class MaterialStock(BaseSubgraph):
    context_type = HistoricalContext
```
```json
{ "subgraphs": { "material.stock": { "window": "PT24H" } } }
```

그러면 `state.scoped`에 `start_dt`/`end_dt`가 채워집니다. `window`는 ISO 8601 duration (`PT24H` = 24시간, `P7D` = 7일)이고, **`as_of` 기준 절대 시각으로 변환**되므로 재실행해도 같은 구간을 봅니다.

### process 안에서 LLM 호출하기

지금까지 만든 판정은 전부 코드가 내렸습니다. 규칙으로 표현할 수 있으면 그게 맞습니다 — 빠르고, 싸고, 항상 같은 답이 나오니까요.

하지만 **규칙으로 못 쓰는 판단**도 있습니다. "자재 A와 B가 같은 라인에서 동시에 떨어지는 조합이 위험한가" 같은 건 임계치로 표현이 안 됩니다. 그런 것만 LLM에게 넘깁니다.

이 코드는 [stock.py](../src/application/subgraphs/material/stock.py)에 **이미 구현되어 있고 기본은 꺼져 있습니다.** config 한 줄로 켜집니다.

```json
{ "subgraphs": { "material.stock": { "use_llm_judge": true } } }
```

구현은 이렇게 생겼습니다.

```python
class MaterialStockConfig(SubgraphConfig):
    warn_hours: float = 8.0
    critical_hours: float = 2.0
    use_llm_judge: bool = False        # ← 기본 off


    async def process(self, state: SubgraphState) -> dict:
        ...                             # 임계치 판정은 위에서 코드가 끝냄

        if cfg.use_llm_judge and records:
            judgements.extend(await self._judge_combinations(records))

        return {"records": records, "metrics": metrics, "judgements": judgements}


    async def _judge_combinations(self, records: list) -> list[Judgement]:
        facts = "\n".join(
            f"- [{r.id}] {r.metadata['line']} {r.metadata['material']}: "
            f"재고 {r.record['stock_qty']:,}ea, "
            f"시간당 {r.record['hourly_consumption']}ea 소비"
            for r in records
        )
        prompt = (
            "아래는 라인별 자재 재고 현황입니다. 개별 임계치로는 잡히지 않는 "
            "위험 조합(같은 라인의 자재가 동시에 소진되는 경우 등)이 있는지 "
            "판단하세요. 근거는 반드시 대괄호 안의 id로만 인용하세요.\n"
            f"{facts}"
        )
        return await self.deps.llm.judge(
            self.registry_name, prompt, [r.id for r in records]
        )
```

지켜야 할 것이 셋입니다.

**① 프롬프트에 id를 함께 싣습니다** — `[stock:L1:MAT-A]` 형태로요. LLM이 근거를 인용할 때 쓸 어휘를 주는 겁니다. id 없이 사실만 주면 LLM은 근거를 지어낼 수밖에 없습니다.

**② 사실은 `- `로 시작하는 줄로 줍니다.** 지시문과 데이터를 구분하는 약속입니다.

**③ `judge`의 세 번째 인자가 허용된 id 목록입니다.** LLM이 여기 없는 id를 대면 **그 판정은 폐기**됩니다.

```
⚠ 가드레일: material.stock: 근거 ['stock:L1:MAT-B::hallucinated']가 입력에 없어 판정을 폐기했습니다
```

이 검증은 **LLM 없이 코드로 돕니다.** 그래서 LLM이 아무리 그럴듯하게 지어내도 리포트까지 새지 않습니다. (가짜 LLM이 동작을 보여주려고 일부러 없는 id를 하나 섞습니다. 실제 LLM에서는 존재하지 않는 데이터를 인용할 때 뜹니다.)

켜고 돌리면 코드 판정 옆에 LLM 판정이 함께 붙습니다.

```
판정 2건
  - L2 MAT-A 입고 전 소진 우려 · 근거 ['stock:L2:MAT-A']    ← 코드
  - 판정 #1 · 근거 ['stock:L1:MAT-A']                        ← LLM
```

(건수는 `as_of`와 공장별 임계치에 따라 달라집니다. 가짜 LLM은 `subject`를 `판정 #N`으로만 채웁니다.)

반환된 `Judgement`는 코드가 만든 것과 **똑같은 스키마**라서 리포트에서 구분 없이 섞이고, 근거 추적도 동일하게 됩니다.

또 다른 사례는 [kpi/check.py](../src/application/subgraphs/kpi/check.py)를 보세요 — 거기는 기본이 켜져 있습니다.

> **지금은 가짜 LLM이 답합니다.** 실제 LLM으로 바꾸려면 config에 `"llm": {"adapter": "chat_model"}`을 넣고 `.env`에 `LLM_BASE_URL`·`LLM_API_KEY`를 채우면 됩니다. 자격증명이 없으면 그 서브그래프만 부분 실패합니다. **서브그래프 코드는 한 글자도 안 바뀝니다** — `self.deps.llm`이 포트라서요.

### 이 서브그래프만 다른 모델 쓰기

프롬프트를 다듬는 동안 **이 분석만** 실제 LLM에 붙이고 나머지는 가짜로 두고 싶을 때가 있습니다. 서브그래프 블록에 `llm`을 넣으면 됩니다.

```json
{
  "llm": { "adapter": "fake" },
  "subgraphs": {
    "material.stock": {
      "enabled": true,
      "use_llm_judge": true,
      "llm": { "adapter": "chat_model", "model": "gpt-4o" }
    }
  }
}
```

기본 설정 위에 deep merge되므로 **바꿀 키만** 적으면 됩니다. 모델만 올리고 싶으면 `{"model": "gpt-4o"}`만 쓰면 `adapter`는 기본값을 물려받습니다.

어떤 모델이 실제로 쓰였는지는 `LLMTrace`에 남아 State에 쌓입니다. 자세한 건 [README의 LLM 연결과 모델 교체](../README.md#llm-연결과-모델-교체)를 보세요.

### 특정 공장만 다른 로직

임계치가 아니라 **로직 자체**가 달라야 하면, 상속으로 만들고 config에서 갈아끼웁니다. 이 저장소에 실제 예시가 있습니다 — [material/stock_gumi.py](../src/application/subgraphs/material/stock_gumi.py)는 구미 공장의 자재 입고 주기(하루 두 번)를 반영해 "다음 입고까지 버티는가"로 판정합니다.

```python
# subgraphs/material/stock_gumi.py    → "material.stock_gumi"
@register(slot_only=True)              # ← 이 표시가 중요합니다 (아래 설명)
class GumiMaterialStock(MaterialStock):
    async def process(self, state):
        ...
```
```json
// config/factories/gumi/mx.json
{ "subgraphs": { "material.stock": { "nodes": { "process": "material.stock_gumi" } } } }
```

돌려보면 같은 `material.stock`인데 공장마다 다른 로직이 붙습니다.

```
mx/gumi  → 재고 1,263ea · 다음 입고까지 1시간 · 여유 +4.8시간     (구미 전용)
mx/asan  → 재고 1,263ea · 시간당 219ea 소비                      (기본)
```

**`slot_only=True`를 빠뜨리면 다른 공장에서 부팅이 막힙니다.** 이 클래스는 구미 config에서만 참조되므로, 아산에서 돌릴 때 "어느 config에도 없는 고아"로 잡히기 때문입니다. `slot_only`는 "독립 실행용이 아니라 슬롯 부품"이라는 선언입니다.

**상속은 코드에서, 선택은 config에서.** 슬롯 이름은 `validate` / `process` / `output` / `error` 넷이고, 대상으로는 **공유 노드 부품**(`outputs.no_llm`)과 **다른 서브그래프**(`material.stock_gumi`) 둘 다 쓸 수 있습니다. `python -m src registry`가 쓸 수 있는 이름을 전부 보여줍니다.

### 서술 없이 표만 내기

LLM 호출을 줄이고 싶으면 `output` 슬롯을 공유 부품으로 바꿉니다.

```json
{ "subgraphs": { "material.stock": { "nodes": { "output": "outputs.no_llm" } } } }
```

지표와 판정은 그대로 나오고 LLM 서술만 빠집니다.

---

## 체크리스트

새 분석을 추가할 때:

- [ ] `subgraphs/<폴더>/<파일>.py` — 경로가 곧 등록명
- [ ] `SubgraphConfig`를 상속한 config 스키마
- [ ] `@register()` + `title` + `config_model` + `context_type`
- [ ] `process`에서 `{records, metrics, judgements}` 반환
- [ ] 숫자는 `metrics`에, 판정 근거는 실제 `Record.id`로
- [ ] `config/gbm/<gbm>.json`에 `"enabled": true`와 함께 등록
- [ ] `python -m src registry`로 등록 확인
- [ ] `python -m src run`으로 리포트 확인

규칙으로 못 쓰는 판단이 있으면:

- [ ] config에 `use_llm_judge` 같은 플래그를 두고 **기본은 off**
- [ ] 프롬프트에 `[id]`를 함께 싣고, `judge()`에 허용 id 목록 전달

새 데이터가 필요하면 앞에 하나 더:

- [ ] 어댑터에 `kind` 추가 (실제 구현이면 쿼리, 스텁이면 `fake_data`)

---

## 다음으로

- [README](../README.md) — 전체 구조, 설정 두 축(config/`.env`), 리포트 템플릿, 실제 DB 연결로 전환
- [설계 문서](superpowers/specs/2026-08-13-langgraph-report-template-design.md) — 왜 이렇게 설계했는지, 검토했다가 버린 대안들
