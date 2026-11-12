import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a test tries to open a real connection."""
    import urllib.request

    def _blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network access attempted in test run")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
