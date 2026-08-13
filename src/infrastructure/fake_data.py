"""가짜 데이터 저장소.

실제 구현에서는 이 모듈이 통째로 사라지고 각 어댑터가 실제 서버에
붙는다. 지금은 dict 조회로 대신한다.

as_of로 난수 시드를 고정하므로 **같은 as_of는 항상 같은 데이터**를
돌려준다. 재실행 멱등성과 replay를 예시에서도 그대로 확인할 수 있다.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

LINES = ["L1", "L2", "L3"]
EQUIPMENT = {
    "L1": ["L1-MOUNT-01", "L1-MOUNT-02", "L1-TEST-01", "L1-PACK-01"],
    "L2": ["L2-MOUNT-01", "L2-TEST-01", "L2-TEST-02", "L2-PACK-01"],
    "L3": ["L3-MOUNT-01", "L3-TEST-01", "L3-PACK-01"],
}
EQUIP_STATES = ["RUN", "IDLE", "DOWN", "ALARM"]

KAFKA_GROUPS = [
    ("mes-ingest", "mes.equipment.status", 6),
    ("kpi-aggregator", "mes.production.kpi", 3),
    ("alarm-consumer", "mes.alarm.raised", 3),
]

ALARM_SCENARIOS = [
    ("SCN-1001", "설비 온도 임계 초과"),
    ("SCN-1002", "자재 공급 지연"),
    ("SCN-1003", "비전 검사 불량 급증"),
    ("SCN-1004", "컨베이어 정지"),
]


def _rng(as_of: datetime, salt: str) -> random.Random:
    """as_of + salt로 결정적 난수원을 만든다."""
    return random.Random(f"{as_of.isoformat()}|{salt}")


# --------------------------------------------------------------------------
# Redis: 생산정보 / 라인별 정보 / 장비 상태
# --------------------------------------------------------------------------
def redis_production(as_of: datetime) -> list[dict]:
    rng = _rng(as_of, "production")
    rows = []
    for line in LINES:
        target = rng.choice([1200, 1500, 1800])
        actual = int(target * rng.uniform(0.72, 1.03))
        rows.append(
            {
                "id": f"prod:{line}",
                "metadata": {"line": line, "shift": "DAY"},
                "record": {
                    "target_qty": target,
                    "actual_qty": actual,
                    "achievement_pct": round(actual / target * 100, 1),
                    "yield_pct": round(rng.uniform(93.0, 99.5), 2),
                },
            }
        )
    return rows


def redis_line_info(as_of: datetime) -> list[dict]:
    rng = _rng(as_of, "line")
    rows = []
    for line in LINES:
        rows.append(
            {
                "id": f"line:{line}",
                "metadata": {"line": line},
                "record": {
                    "model": rng.choice(["SM-A56", "SM-S25", "SM-F76"]),
                    "operators": rng.randint(4, 9),
                    "uptime_pct": round(rng.uniform(78.0, 99.0), 1),
                },
            }
        )
    return rows


def redis_material_stock(as_of: datetime) -> list[dict]:
    """라인별 자재 재고. docs/tutorial.md에서 예시로 쓰는 데이터."""
    rng = _rng(as_of, "material")
    rows = []
    for line in LINES:
        for material in ("MAT-A", "MAT-B"):
            rows.append(
                {
                    "id": f"stock:{line}:{material}",
                    "metadata": {"line": line, "material": material},
                    "record": {
                        "stock_qty": rng.randint(120, 5_000),
                        "hourly_consumption": rng.randint(80, 400),
                    },
                }
            )
    return rows


def redis_equipment_status(as_of: datetime) -> list[dict]:
    rng = _rng(as_of, "equipment")
    rows = []
    for line, equips in EQUIPMENT.items():
        for eq in equips:
            state = rng.choices(EQUIP_STATES, weights=[70, 15, 8, 7])[0]
            rows.append(
                {
                    "id": f"equip:{eq}",
                    "metadata": {"line": line, "equipment_id": eq},
                    "record": {
                        "state": state,
                        "since_min": rng.randint(1, 240),
                        "temperature_c": round(rng.uniform(28.0, 78.0), 1),
                    },
                }
            )
    return rows


# --------------------------------------------------------------------------
# Kafka AdminClient: 오프셋 메타데이터만. 메시지는 소비하지 않는다.
# --------------------------------------------------------------------------
def kafka_consumer_lag(as_of: datetime) -> list[dict]:
    rng = _rng(as_of, "kafka")
    rows = []
    for group, topic, partitions in KAFKA_GROUPS:
        for p in range(partitions):
            end_offset = rng.randint(500_000, 900_000)
            lag = rng.choices(
                [rng.randint(0, 500), rng.randint(5_000, 60_000)],
                weights=[80, 20],
            )[0]
            rows.append(
                {
                    "id": f"lag:{group}:{topic}:{p}",
                    "metadata": {"group": group, "topic": topic, "partition": p},
                    "record": {
                        "end_offset": end_offset,
                        "committed_offset": end_offset - lag,
                        "lag": lag,
                    },
                }
            )
    return rows


# --------------------------------------------------------------------------
# REST API: KPI
# --------------------------------------------------------------------------
def rest_kpis(as_of: datetime) -> list[dict]:
    rng = _rng(as_of, "kpi")
    specs = [
        ("OEE", "%", 85.0, (70.0, 95.0)),
        ("가동률", "%", 90.0, (75.0, 99.0)),
        ("수율", "%", 97.0, (92.0, 99.8)),
        ("UPH", "ea/h", 320.0, (240.0, 380.0)),
        ("직행률", "%", 95.0, (88.0, 99.0)),
    ]
    rows = []
    for name, unit, target, (lo, hi) in specs:
        value = round(rng.uniform(lo, hi), 1)
        rows.append(
            {
                "id": f"kpi:{name}",
                "metadata": {"kpi": name, "unit": unit},
                "record": {
                    "value": value,
                    "target": target,
                    "gap_pct": round((value - target) / target * 100, 1),
                },
            }
        )
    return rows


# --------------------------------------------------------------------------
# MongoDB: 알람 이력 (구간 조회)
# --------------------------------------------------------------------------
def mongo_alarms(start_dt: datetime, end_dt: datetime) -> list[dict]:
    """구간 안의 알람을 돌려준다. historical 조회는 Kafka가 아니라 여기서 한다."""
    rng = _rng(end_dt, "alarm")
    rows = []
    days = max(1, (end_dt - start_dt).days)
    for day in range(days):
        day_start = start_dt + timedelta(days=day)
        for scen_id, title in ALARM_SCENARIOS:
            # 뒤로 갈수록 늘어나는 시나리오를 하나 심어둔다 (추세 확인용)
            base = rng.randint(0, 6)
            if scen_id == "SCN-1003":
                base += day * 2
            for seq in range(base):
                rows.append(
                    {
                        "id": f"alarm:{scen_id}:{day}:{seq}",
                        "metadata": {
                            "scen_id": scen_id,
                            "title": title,
                            "line": rng.choice(LINES),
                            "raised_at": (
                                day_start + timedelta(hours=rng.randint(0, 23))
                            ).isoformat(),
                            "day_offset": day,
                        },
                        "record": {
                            "severity": rng.choice(["MINOR", "MAJOR", "CRITICAL"]),
                            "duration_min": rng.randint(1, 90),
                        },
                    }
                )
    return rows


# --------------------------------------------------------------------------
# 연결 상태 (헬스체크)
# --------------------------------------------------------------------------
def health_snapshot(as_of: datetime, name: str) -> dict:
    rng = _rng(as_of, f"health:{name}")
    # kafka만 가끔 degraded 하도록 해서 부분 실패 표시를 볼 수 있게 한다
    ok = True if name != "kafka" else rng.random() > 0.35
    return {
        "reachable": ok,
        "latency_ms": round(rng.uniform(1.0, 40.0), 1) if ok else None,
        "detail": "" if ok else "연결 시도 시간 초과",
    }
