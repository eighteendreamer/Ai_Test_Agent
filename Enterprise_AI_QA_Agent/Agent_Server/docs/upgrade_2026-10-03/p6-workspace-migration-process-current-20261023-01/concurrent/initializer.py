
import json
from pathlib import Path
import sys
import time

from src.application.settings.workspace_settings_service import WorkspaceSettingsService, GENERAL_SETTINGS_KEY, CHANNEL_ADVANCED_KEY
from src.core.config import get_settings
from src.infrastructure.application_config_store import MySQLApplicationConfigStore

legacy, control, worker, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]

def pause(marker, release):
    (control / marker).write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 30
    while not (control / release).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("migration fixture barrier expired: " + marker)
        time.sleep(.02)

class PausedStore(MySQLApplicationConfigStore):
    def get(self, key, **kwargs):
        record = super().get(key, **kwargs)
        if mode == "race" and record is None and key in {GENERAL_SETTINGS_KEY, CHANNEL_ADVANCED_KEY}:
            pause(worker + "-" + key, "release-" + key)
        return record

    def save(self, key, payload, **kwargs):
        record = super().save(key, payload, **kwargs)
        if mode == "interrupt" and key == GENERAL_SETTINGS_KEY:
            pause(worker + "-committed", "never-release")
        return record

store = PausedStore(get_settings())
WorkspaceSettingsService(store, legacy).initialize()
print(json.dumps({key: store.get(key).revision for key in (GENERAL_SETTINGS_KEY, CHANNEL_ADVANCED_KEY)}))
