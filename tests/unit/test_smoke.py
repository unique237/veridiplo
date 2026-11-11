from app.core.config import get_settings


def test_defaults_offline():
    s = get_settings()
    assert s.LLM_PROVIDER == "none"
    assert not s.cloud_llm
