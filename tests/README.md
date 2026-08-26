# 테스트

```bash
python -m unittest discover -s tests -t .        # 전체
python -m unittest tests.test_boot_validation    # 한 파일
python -m unittest discover -s tests -t . -v     # 자세히
```

가상환경을 활성화하지 않았다면 `python` 자리에 `.venv/bin/python`(Linux·macOS) 또는 `.venv\Scripts\python.exe`(Windows).

`pytest`가 설치돼 있으면 같은 테스트를 그대로 실행하면서 더 나은 출력과 선택 실행을 쓸 수 있습니다.

```bash
pytest tests -q
pytest tests -k "override"        # 이름으로 골라서
pytest tests/test_lock.py -v
```

> 테스트는 표준 라이브러리 `unittest`로 작성돼 있어 pytest 없이도 돕니다.

## 구성

| 파일 | 검증하는 것 |
|---|---|
| [test_config_merge.py](test_config_merge.py) | 3단 deep merge, 값의 출처 추적, 없는 계층 처리 |
| [test_boot_validation.py](test_boot_validation.py) | 이름 대조·고아·스키마·키 오타·슬롯 참조, 문제를 한 번에 모아 보고 |
| [test_graph_behaviour.py](test_graph_behaviour.py) | 부분 실패 격리, on/off, 슬롯·모델 override, 가드레일, 멱등성 |
| [test_subgraph_nodes.py](test_subgraph_nodes.py) | 가짜 포트를 주입해 `process`만 직접 호출 |
| [test_renderer.py](test_renderer.py) | 템플릿 블록 파싱, 여백 보존, 심각도 정렬, 양식 교체 |
| [helpers.py](helpers.py) | 임시 3단 config 생성, 그래프 실행 |

## 외부 의존이 없습니다

DB도 LLM도 Docker도 필요 없고 네트워크도 쓰지 않습니다. 포트가 `Protocol`이고 config가 조립 시점에만 쓰이는 설계 덕분입니다.

`AS_OF`가 고정돼 있고 `fake_data`가 그 값으로 난수 시드를 만들므로 **결과가 항상 재현**됩니다.

**저장소의 `config/`는 절대 수정하지 않습니다.** `temp_config()`가 임시 디렉터리에 3단 계층을 만들고, 실제 `config/gbm/mx.json`을 출발점으로 삼습니다 — config 스키마가 바뀌면 테스트도 함께 깨져야 그 신호를 놓치지 않기 때문입니다.

## 새 분석을 만들 때

[test_subgraph_nodes.py](test_subgraph_nodes.py)를 복사해 시작하세요. 필요한 건 이게 전부입니다.

```python
class FakePort:
    def __init__(self, records): self.records = records
    async def fetch(self, ctx, spec): return self.records

sub = MySubgraph(MyConfig(enabled=True), FakeDeps(redis=FakePort([...])))
out = asyncio.run(sub.process(SubgraphState(ctx=CTX, scoped=CTX)))
```

경계값(임계치에 딱 걸리는 값), 빈 입력, 0으로 나누기를 넣어두면 좋습니다 — 셋 다 실제로 버그가 나온 자리입니다.

## 아직 없는 것

- **어댑터 테스트** — 의도적으로 뺐습니다. 이유와 대가는 [설계 문서 §11](../docs/superpowers/specs/2026-08-13-langgraph-report-template-design.md)에 있습니다.
  예외는 [test_references.py](test_references.py) 하나입니다 — `StaticReferenceAdapter`는 외부 의존 없이 로컬 파일만 읽고, "경로가 틀리면 부팅에서 멈춘다"가 그 어댑터의 핵심 동작이라 검증합니다
- CLI 인자 파싱, 발송 채널 실제 I/O
