import pytest

from llm.base import get_llm
from llm.drivers.anthropic import AnthropicLLM
from llm.drivers.deepseek import DeepSeekLLM
from llm.drivers.gigachat import GigaChatLLM
from llm.drivers.mock import MockLLM


def test_default_provider_is_mock(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(get_llm(), MockLLM)


def test_provider_mock_explicit(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert isinstance(get_llm(), MockLLM)


def test_provider_deepseek_selected_without_key(monkeypatch):
    # Конструктор не обязан трогать сеть — ключ проверяется лениво при вызове.
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    llm = get_llm()
    assert isinstance(llm, DeepSeekLLM)


def test_provider_gigachat_selected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gigachat")
    llm = get_llm()
    assert isinstance(llm, GigaChatLLM)


def test_provider_anthropic_selected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    llm = get_llm()
    assert isinstance(llm, AnthropicLLM)


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValueError):
        get_llm()
