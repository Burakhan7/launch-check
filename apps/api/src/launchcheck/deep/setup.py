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
    from launchcheck.deep.external_module import ExternalModule
    from launchcheck.deep.funnel_module import FunnelModule
    from launchcheck.deep.links_module import LinksModule
    from launchcheck.deep.perf_module import PerfModule
    from launchcheck.deep.privacy_module import PrivacyModule
    from launchcheck.deep.render_module import RenderModule

    # Şerit içi sıra listedeki sıradır: tarayıcı render → gizlilik → huni; ağ dış görünürlük →
    # linkler
    return [
        RenderModule(),
        PrivacyModule(),
        FunnelModule(),
        PerfModule(),
        ExternalModule(),
        LinksModule(),
    ]


def default_verification_rules() -> Mapping[str, VerificationRule]:
    from launchcheck.deep.verification import LINK_RULES, RENDER_RULES

    return {**RENDER_RULES, **LINK_RULES}


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
