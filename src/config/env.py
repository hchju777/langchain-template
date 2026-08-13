"""배포 환경 설정 — 접속 정보와 비밀값.

config JSON과 축이 다르다.

    config JSON : GBM/FCT(사업부·공장)에 따라 갈린다. git에 커밋된다.
                  임계치, 기능 on/off, 템플릿, 스케줄 같은 도메인 설정.
    .env        : 배포 환경(dev/stg/prod)에 따라 갈린다. 커밋되지 않는다.
                  접속 주소, 계정, 비밀번호, 토큰.

같은 mx/gumi라도 dev 서버와 prod 서버는 다른 Mongo를 본다. 그건 3단
merge로 표현할 수 없으므로 이쪽이 담당한다.

판별이 애매하면 두 가지를 물어보면 된다.
    1. git에 커밋해도 되는가?           → 아니면 .env
    2. GBM/FCT가 아니라 환경에 따라 바뀌는가? → 그러면 .env

전부 기본값이 있어 .env 없이도 뜬다(스텁 상태에서 개발하기 위함).
실제 연결로 전환하면 빈 값은 어댑터가 연결 시도 시 실패한다.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # 배포 좌표 — 이 프로세스가 어떤 GBM/FCT로 뜰 것인가.
    # CLI의 --gbm/--factory가 있으면 그쪽이 우선한다.
    # ------------------------------------------------------------------
    gbm: str = Field(default="mx", validation_alias="DEPLOY_GBM")
    factory: str = Field(default="gumi", validation_alias="DEPLOY_FACTORY")

    # ------------------------------------------------------------------
    # Redis — 생산정보·라인정보·장비상태 스냅샷
    # ------------------------------------------------------------------
    redis_url: str = Field(default="", validation_alias="REDIS_URL")
    redis_password: str = Field(default="", validation_alias="REDIS_PWD")

    # ------------------------------------------------------------------
    # MongoDB — 알람 이력(구간 조회) + 체크포인터
    # ------------------------------------------------------------------
    mongodb_uri: str = Field(default="", validation_alias="MONGODB_URI")
    mongodb_password: str = Field(default="", validation_alias="MONGODB_PWD")
    mongodb_database: str = Field(default="mes", validation_alias="MONGODB_DB")
    checkpoint_database: str = Field(
        default="langgraph", validation_alias="CHECKPOINT_DB"
    )

    # ------------------------------------------------------------------
    # Kafka — 오프셋 메타데이터만 읽는다(메시지 소비 안 함)
    # ------------------------------------------------------------------
    kafka_bootstrap_servers: str = Field(
        default="", validation_alias="KAFKA_BOOTSTRAP_SERVERS"
    )
    kafka_username: str = Field(default="", validation_alias="KAFKA_USER")
    kafka_password: str = Field(default="", validation_alias="KAFKA_PWD")

    # ------------------------------------------------------------------
    # REST API — KPI 조회
    # ------------------------------------------------------------------
    rest_base_url: str = Field(default="", validation_alias="REST_BASE_URL")
    rest_api_token: str = Field(default="", validation_alias="REST_API_TOKEN")

    # ------------------------------------------------------------------
    # LLM — 공급자 교체는 base_url만 바꾸면 된다.
    # model/temperature는 GBM/FCT별로 다를 수 있어 config JSON에 둔다.
    # ------------------------------------------------------------------
    llm_base_url: str = Field(default="", validation_alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="", validation_alias="LLM_API_KEY")

    # ------------------------------------------------------------------
    # 메일 발송 — 수신자는 GBM/FCT별로 다르므로 config JSON에 둔다.
    # 여기는 서버 접속 정보만.
    # ------------------------------------------------------------------
    smtp_host: str = Field(default="", validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=25, validation_alias="SMTP_PORT")
    smtp_username: str = Field(default="", validation_alias="SMTP_USER")
    smtp_password: str = Field(default="", validation_alias="SMTP_PWD")
    smtp_sender: str = Field(default="", validation_alias="SMTP_SENDER")
