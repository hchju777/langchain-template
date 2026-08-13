"""전역 상수. 매직값을 코드에 직접 쓰지 않는다."""

from pathlib import Path

# 경로
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = Path(__file__).resolve().parent
CONFIG_ROOT = PROJECT_ROOT / "config"
TEMPLATE_ROOT = SRC_ROOT / "presentation" / "templates"
OUTPUT_ROOT = PROJECT_ROOT / "output"

# config 병합 계층 (뒤가 앞을 덮어씀)
CONFIG_LAYER_GBM = "gbm"
CONFIG_LAYER_FACTORY_COMMON = "factory_common"
CONFIG_LAYER_FACTORY_GBM = "factory_gbm"

# 서브그래프 4슬롯
SLOT_VALIDATE = "validate"
SLOT_PROCESS = "process"
SLOT_OUTPUT = "output"
SLOT_ERROR = "error"

# config 키
KEY_SUBGRAPHS = "subgraphs"
KEY_ENABLED = "enabled"
KEY_NODES = "nodes"
KEY_PORTS = "ports"
KEY_LLM = "llm"
KEY_REPORT = "report"
KEY_DELIVERY = "delivery"
KEY_STORES = "stores"
KEY_TIMEOUT_SEC = "timeout_sec"
#: config에 블록은 있지만 아직 읽는 코드가 없다. 상주 스케줄러를 붙일 때 쓴다.
KEY_SCHEDULE = "schedule"

# 어댑터 기본 정책
DEFAULT_TIMEOUT_SEC = 5.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_BACKOFF_BASE_SEC = 0.2

# 리포트
DEFAULT_REPORT_TEMPLATE = "report.md"
