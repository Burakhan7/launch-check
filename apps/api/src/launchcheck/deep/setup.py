"""Üretimde kullanılan Faz 2 modülleri ve doğrulama kuralları.

Modüller milestone'larla eklenir (P3 render, P4 hız, ...). P1'de liste boştur: orkestratör
Faz 1 bulgularını kopyalar, olayları yayınlar ve taramayı uçtan uca tamamlar.
"""

from collections.abc import Mapping
from pathlib import Path

from launchcheck.checks.registry import load_builtin_checks
from launchcheck.config import Settings
from launchcheck.deep.assets import AssetStore
from launchcheck.deep.modules import DeepModule, VerificationRule
from launchcheck.deep.orchestrator import OrchestratorConfig


def default_modules() -> list[DeepModule]:
    return []


def default_verification_rules() -> Mapping[str, VerificationRule]:
    return {}


def asset_store(settings: Settings) -> AssetStore:
    return AssetStore(Path(settings.assets_dir), settings.deep_max_asset_bytes)


def orchestrator_config(settings: Settings) -> OrchestratorConfig:
    registry = load_builtin_checks()
    return OrchestratorConfig(
        settings=settings,
        modules=default_modules(),
        verification_rules=default_verification_rules(),
        special_data_aware=[c.id for c in registry.all() if c.considers_special_data],
        assets=asset_store(settings),
    )
