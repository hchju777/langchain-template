# # 초안
## 주요 특징
- 주요 목적
    - 어떤 서비스의 운영을 위해 AI를 통해 현재 상태를 분석하는 시스템 구축
    - 데이터를 가져와서 분석한 후 이를 요약해서 Reporting
    - Report는 메일이나 md형식의 파일로 나옴
- 데이터를 가져오기 위해서는 아래와 같은 것들에서 데이터를 가져옴
    - Redis (Async)
    - MongoDB (Async)
    - Kafka (Async)
    - REST API (Async)
- Hexagonal Architecture + Clean Architecture 적용
- GBM/FCT 구조에서 GBM/FCT에 맞게 Deep merge를 통해 config에서 꺼내서 쓸 수 있어야 함
- GBM/FCT별로 공통될수도, 공통되지 않을 수도 있는 기능이 있음. Config를 통해 껐다켰다가 편해야 함. 다른 GBM/FCT에도 공통적으로 적용할 수 있어야 함
- Mapper가 존재해서 각 Node 별로 매핑 + GBM/FCT 별로도 매핑
- 여러 독립적인 Subgraph가 있어서 각각 데이터 분석을 마치면 그 결과를 마지막에 취합해서 보고서 작성함
- 하드코딩되지 말 것: 적어도 파이썬 파일 위에 상수로 이름을 넣고 상수를 가져다 쓸 것
- Scheduling Job도 가능해야 함 -> Batch 성으로 매일 8시 레포트 생성 등
- 병렬 실행도 가능할 것으로 보임
- Command: ㅏ상태 업데이트도 함께: 원자성
- 신규 기능 추가/로직 변경/On, OFF가 매우 쉬워야 함(confi로 바로바로 수정이 가능할 정도. 로직은 파일추가 후 config에 기입을 하면 되도록)
- Template (md파일형탴) 존재 가능
- 기본적으로 validate input node / process node(subgraph) / generate output / handle_error가 있어서 그게 하나의 subgraph가 되어야 함
- Time Travle로 추론 과정 이해 / 오류 디버깅 / 대안 탐색 가능: Fork, 품질 검수 후 최종 출력, 최적 결과 찾기 가능, 오류 복구 가능, 디버깅 
- Use **kwargs to get additional params
워크플로우
- Durable Execution: 중단된 지점부터 재개할 수 있도록 함 / Side Effects 처리: 이메일 발송이나 파일 쓰기 등은 멱등성 보장 필요
- Streaming: 그래프 상태를 실시간으로 모니터링
- State, Node 등은 기본적으로 상속을 지향
## State
- 상속 지향
- Pydantic Schema 활용
- Input schema, output schema 적용
- Reducer 적용: 어떻게 state를 업데이트 할 것인가? Custom Reducer는 따로 관리하는 것인가?
### Node
- 노드는 기본적인 노드가 있고 이를 데코레이터, 팩토리 등을 활용해서 다양한 노드를 입맛대로 쓸 수 있도록 함
- 데코레이터 패턴 노드
    - 관심사 분리와 조합 가능성
    - 비즈니스 로직과 인프라 관심사 명확히 분리
    - @timing_decorator / @error_handling_decorator 함께 사용해서 둘 다 가능
- 팩토리 패턴 노드 사용 가능
    - 런타임이나 다양한 타입의 노드를 동적으로 생성
    - 설정이나 조건에 따라 다른 동작을 하는 노드들을 일관된 인터페이스로 생성
    - 팩토리 함수는 노드 타입과 매개변수를 받아 해당하는 노드 함수를 생성하고 반환
    - 노드 생성 로직을 중앙화, 유연성과 확장성 추가 가능, 동적으로 그래프 구조 구성
- Cache 적용 가능: 각 노드마다 캐시 다르게 적용, config에 기록
- 기본적으로 metadata 안에 메타데이터를 담고, record 안에 실제 데이터를 담을 것
### Edge
- 조건부 엣지, Command Send API

# Ref Code
## DeployConfig.py
```python
gbm + factories/{factory}/common + factories/{factory}/{gbm} deep merge

import json
from pathlib import Path

_CONFIG_ROOT = Path(__file__).resolve().parent.parentnt/"config"

def _depp_merge_dict(base_dict: dict, update_dict: dict) -> dict:
    """Deep merge two dictionary. update_dict is the first"""
    result = base_dict.copy()
    for key, value in update_dict.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result

def _load_json(path: Path) _. dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}

class DeployConfig:
    def __init__(self, gbm: str, factory: str) -> None:
        self.gbm = gbm
        self.factory = factory
        self.config = self._load_merged_config()
    
    def get(self, *keys, default = None):
        value = self.config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
        return value if value is not None else default
    
    def _load_merged_config(self) -> dict
        config: dict = {}
        gbm_path = _CONFIG_ROOT / "gbm" / f"{self.gbm}.json"
        config = _deep_merge_dict(config, _load_json(common_path))

        factory_gbm_path = _CONFIG_ROOT / "factories" / self.factory / f"{self.gbm}.json"
        config = _deep_merge_dict(config, _load_json(factory_gbm_path))

        return config
```

## EnvConfig.py
```python
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class EnvConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gbm: str = Field(default="mx", validation_alias="DEPLOY_GBM")
    factory: str = Field(default="gumi", validation_alias="DEPLOY_FACTORY")
    redis_password: str = Field("", validation_alias="REDIS_PWD")
    mongodb_password: str = Field("", validation_alias="MONGODB_PWD")
```