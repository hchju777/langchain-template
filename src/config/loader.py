"""3단 deep merge 로더.

    config/gbm/{gbm}.json
      → config/factories/{factory}/common.json
        → config/factories/{factory}/{gbm}.json

뒤가 앞을 덮어쓴다. 값마다 "어느 파일에서 왔는지"를 함께 추적하는데,
3단 병합에서 "이 값이 왜 이래?"는 반드시 나오는 질문이기 때문이다.
그게 없으면 파일 셋을 눈으로 대조하게 된다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.constants import (
    CONFIG_LAYER_FACTORY_COMMON,
    CONFIG_LAYER_FACTORY_GBM,
    CONFIG_LAYER_GBM,
    CONFIG_ROOT,
)


def deep_merge(base: dict, update: dict) -> dict:
    """중첩 dict를 재귀적으로 병합한다. update 쪽이 우선한다."""
    result = dict(base)
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _trace_origins(data: dict, layer: str, prefix: str = "") -> dict[str, str]:
    """리프 값마다 어느 계층에서 왔는지 기록한다."""
    origins: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            origins.update(_trace_origins(value, layer, path))
        else:
            origins[path] = layer
    return origins


class DeployConfig:
    """GBM/FCT에 맞춰 병합된 설정."""

    def __init__(self, gbm: str, factory: str, root: Path | None = None) -> None:
        self.gbm = gbm
        self.factory = factory
        self._root = root or CONFIG_ROOT
        self.origins: dict[str, str] = {}
        self.layer_files: dict[str, Path] = {}
        self.data = self._load_merged()

    @property
    def root(self) -> Path:
        """이 설정을 읽어온 config 디렉터리. 진단과 테스트가 쓴다."""
        return self._root

    def _layer_paths(self) -> list[tuple[str, Path]]:
        return [
            (CONFIG_LAYER_GBM, self._root / "gbm" / f"{self.gbm}.json"),
            (
                CONFIG_LAYER_FACTORY_COMMON,
                self._root / "factories" / self.factory / "common.json",
            ),
            (
                CONFIG_LAYER_FACTORY_GBM,
                self._root / "factories" / self.factory / f"{self.gbm}.json",
            ),
        ]

    def _load_merged(self) -> dict:
        merged: dict = {}
        for layer, path in self._layer_paths():
            self.layer_files[layer] = path
            loaded = _load_json(path)
            if not loaded:
                continue
            merged = deep_merge(merged, loaded)
            self.origins.update(_trace_origins(loaded, layer))
        return merged

    def get(self, *keys: str, default: Any = None) -> Any:
        value: Any = self.data
        for key in keys:
            if not isinstance(value, dict):
                return default
            value = value.get(key)
            if value is None:
                return default
        return value

    def section(self, *keys: str) -> dict:
        value = self.get(*keys, default={})
        return value if isinstance(value, dict) else {}

    def origin_of(self, dotted_key: str) -> str:
        return self.origins.get(dotted_key, "(기본값)")
