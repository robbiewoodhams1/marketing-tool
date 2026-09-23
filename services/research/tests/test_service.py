import research
from research.config import Settings
from research.logging import get_logger
from research.main import main


def test_package_imports():
    assert research.__version__


def test_settings_from_env_defaults_and_overrides():
    assert Settings.from_env({}).log_level == "INFO"
    assert Settings.from_env({}).supabase_url is None
    s = Settings.from_env({"RESEARCH_LOG_LEVEL": "debug", "SUPABASE_URL": "http://x"})
    assert s.log_level == "DEBUG"
    assert s.supabase_url == "http://x"


def test_get_logger_is_namespaced():
    assert get_logger("search").name == "research.search"
    assert get_logger("research.main").name == "research.main"


def test_main_runs_and_reports_running(capsys):
    assert main() == 0
    assert "is running" in capsys.readouterr().err
