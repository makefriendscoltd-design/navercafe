import pytest


@pytest.fixture(autouse=True)
def _pin_youtube_browser_backend(monkeypatch):
    """Tests exercise the injected/Aside contracts; never reach live Ego Lite."""
    monkeypatch.setenv("YOUTUBE_SHORTS_BROWSER", "aside")
