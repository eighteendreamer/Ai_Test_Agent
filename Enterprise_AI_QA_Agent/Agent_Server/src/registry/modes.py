from __future__ import annotations

from dataclasses import dataclass

from src.modes.api_testing_mode import MODE_MANIFEST as API_TESTING_MODE_MANIFEST
from src.modes.code_review_mode import MODE_MANIFEST as CODE_REVIEW_MODE_MANIFEST
from src.modes.compatibility_testing_mode import MODE_MANIFEST as COMPATIBILITY_TESTING_MODE_MANIFEST
from src.modes.default_mode import MODE_MANIFEST as DEFAULT_MODE_MANIFEST
from src.modes.accessibility_testing_mode import MODE_MANIFEST as ACCESSIBILITY_TESTING_MODE_MANIFEST
from src.modes.integration_testing_mode import MODE_MANIFEST as INTEGRATION_TESTING_MODE_MANIFEST
from src.modes.mobile_testing_mode import MODE_MANIFEST as MOBILE_TESTING_MODE_MANIFEST
from src.modes.performance_testing_mode import MODE_MANIFEST as PERFORMANCE_TESTING_MODE_MANIFEST
from src.modes.reliability_testing_mode import MODE_MANIFEST as RELIABILITY_TESTING_MODE_MANIFEST
from src.modes.security_testing_mode import MODE_MANIFEST as SECURITY_TESTING_MODE_MANIFEST
from src.modes.smoke_testing_mode import MODE_MANIFEST as SMOKE_TESTING_MODE_MANIFEST
from src.modes.unit_component_testing_mode import MODE_MANIFEST as UNIT_COMPONENT_TESTING_MODE_MANIFEST
from src.modes.ui_automation_mode import MODE_MANIFEST as UI_AUTOMATION_MODE_MANIFEST
from src.modes.visual_regression_testing_mode import MODE_MANIFEST as VISUAL_REGRESSION_TESTING_MODE_MANIFEST
from src.schemas.mode import ModeDescriptor


@dataclass(frozen=True)
class ModeModule:
    descriptor: ModeDescriptor


class ModeRegistry:
    def __init__(self) -> None:
        manifests = [
            DEFAULT_MODE_MANIFEST,
            CODE_REVIEW_MODE_MANIFEST,
            UI_AUTOMATION_MODE_MANIFEST,
            API_TESTING_MODE_MANIFEST,
            COMPATIBILITY_TESTING_MODE_MANIFEST,
            UNIT_COMPONENT_TESTING_MODE_MANIFEST,
            ACCESSIBILITY_TESTING_MODE_MANIFEST,
            VISUAL_REGRESSION_TESTING_MODE_MANIFEST,
            MOBILE_TESTING_MODE_MANIFEST,
            RELIABILITY_TESTING_MODE_MANIFEST,
            INTEGRATION_TESTING_MODE_MANIFEST,
            SECURITY_TESTING_MODE_MANIFEST,
            PERFORMANCE_TESTING_MODE_MANIFEST,
            SMOKE_TESTING_MODE_MANIFEST,
        ]
        self._modes: dict[str, ModeModule] = {
            manifest["key"]: ModeModule(descriptor=ModeDescriptor.model_validate(manifest))
            for manifest in manifests
        }

    def list(self) -> list[ModeDescriptor]:
        return [module.descriptor for module in self._modes.values()]

    def get(self, key: str) -> ModeDescriptor:
        normalized_key = (key or "default").strip() or "default"
        if normalized_key not in self._modes:
            raise KeyError(f"Unknown mode: {normalized_key}")
        return self._modes[normalized_key].descriptor

    def resolve(self, key: str | None) -> ModeDescriptor:
        if key:
            return self.get(key)
        return self.get("default")
