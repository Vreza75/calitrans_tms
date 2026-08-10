"""Tests LLMWrapper against a fake OpenAI client, never the real API - the
wrapper is built directly via __new__ so no OPENAI_API_KEY or Streamlit
runtime is required."""
import ai_core.llm as llm_module


def _wrapper(client):
    instance = llm_module.LLMWrapper.__new__(llm_module.LLMWrapper)
    instance.client = client
    instance.default_model = "gpt-4.1-mini"
    return instance


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, output_text, usage=None):
        self.output_text = output_text
        self.usage = usage


class _FakeResponses:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self.responses = _FakeResponses(response=response, exc=exc)


def test_generate_json_returns_missing_key_error_when_client_absent(monkeypatch):
    monkeypatch.setattr(llm_module, "log_ai_usage", lambda **kwargs: None)
    wrapper = _wrapper(client=None)

    result = wrapper.generate_json(task="classify", system_prompt="sys", user_payload={})

    assert result["ok"] is False
    assert "OPENAI_API_KEY" in result["error"]
    assert result["attempts"] == 0


def test_generate_json_success_parses_json_output(monkeypatch):
    logged = {}
    monkeypatch.setattr(llm_module, "log_ai_usage", lambda **kwargs: logged.update(kwargs))
    fake_response = _FakeResponse('{"result": "ok"}', usage=_FakeUsage(10, 5))
    wrapper = _wrapper(client=_FakeClient(response=fake_response))

    result = wrapper.generate_json(task="classify", system_prompt="sys", user_payload={"a": 1})

    assert result["ok"] is True
    assert result["output_json"] == {"result": "ok"}
    assert result["attempts"] == 1
    assert logged["ok"] is True
    assert logged["input_tokens"] == 10
    assert logged["output_tokens"] == 5


def test_generate_json_falls_back_to_raw_text_on_invalid_json(monkeypatch):
    monkeypatch.setattr(llm_module, "log_ai_usage", lambda **kwargs: None)
    wrapper = _wrapper(client=_FakeClient(response=_FakeResponse("not json")))

    result = wrapper.generate_json(task="classify", system_prompt="sys", user_payload={})

    assert result["ok"] is True
    assert result["output_json"]["json_parse_error"] is True
    assert result["output_json"]["raw_text"] == "not json"


def test_generate_json_retries_then_reports_error(monkeypatch):
    monkeypatch.setattr(llm_module.time, "sleep", lambda *_: None)
    logged = {}
    monkeypatch.setattr(llm_module, "log_ai_usage", lambda **kwargs: logged.update(kwargs))
    wrapper = _wrapper(client=_FakeClient(exc=RuntimeError("boom")))

    result = wrapper.generate_json(
        task="classify", system_prompt="sys", user_payload={}, max_retries=1
    )

    assert result["ok"] is False
    assert "boom" in result["error"]
    assert result["attempts"] == 2  # max_retries + 1
    assert logged["ok"] is False


def test_generate_text_returns_missing_key_error_when_client_absent(monkeypatch):
    monkeypatch.setattr(llm_module, "log_ai_usage", lambda **kwargs: None)
    wrapper = _wrapper(client=None)

    result = wrapper.generate_text(task="draft_reply", system_prompt="sys", user_payload={})

    assert result["ok"] is False
    assert "OPENAI_API_KEY" in result["error"]


def test_generate_text_success_returns_output_text(monkeypatch):
    monkeypatch.setattr(llm_module, "log_ai_usage", lambda **kwargs: None)
    fake_response = _FakeResponse("Hello dispatcher", usage=_FakeUsage(3, 2))
    wrapper = _wrapper(client=_FakeClient(response=fake_response))

    result = wrapper.generate_text(task="draft_reply", system_prompt="sys", user_payload={})

    assert result["ok"] is True
    assert result["output_text"] == "Hello dispatcher"


def test_generate_text_retries_then_reports_error(monkeypatch):
    """Regression: after every retry raised, the old tail code fell
    through into dead code referencing `response`/`attempt` from the
    exhausted loop scope - a genuine NameError on this exact path, not
    just an incorrect result. Failure must be reported cleanly instead."""
    monkeypatch.setattr(llm_module.time, "sleep", lambda *_: None)
    logged = {}
    monkeypatch.setattr(llm_module, "log_ai_usage", lambda **kwargs: logged.update(kwargs))
    wrapper = _wrapper(client=_FakeClient(exc=RuntimeError("boom")))

    result = wrapper.generate_text(
        task="draft_reply", system_prompt="sys", user_payload={}, max_retries=1
    )

    assert result["ok"] is False
    assert "boom" in result["error"]
    assert result["attempts"] == 2  # max_retries + 1
    assert logged["ok"] is False
    assert logged["metadata"]["mode"] == "text"


def test_llm_wrapper_reads_api_key_from_config_get_secret(monkeypatch):
    """No streamlit import in this module anymore - api key resolution
    goes through the same canonical config.get_secret() every other
    module uses, not st.secrets."""
    monkeypatch.setattr(llm_module, "get_secret", lambda name: {"OPENAI_API_KEY": "test-key", "OPENAI_RESPONSE_MODEL": None}.get(name))

    wrapper = llm_module.LLMWrapper()

    assert wrapper.api_key == "test-key"
    assert wrapper.default_model == "gpt-4.1-mini"
    assert wrapper.client is not None


def test_llm_wrapper_has_no_client_when_api_key_missing(monkeypatch):
    monkeypatch.setattr(llm_module, "get_secret", lambda name: None)

    wrapper = llm_module.LLMWrapper()

    assert wrapper.api_key == ""
    assert wrapper.client is None


def test_get_llm_returns_the_same_singleton_across_calls(monkeypatch):
    monkeypatch.setattr(llm_module, "_llm_singleton", None)
    monkeypatch.setattr(llm_module, "get_secret", lambda name: None)

    first = llm_module.get_llm()
    second = llm_module.get_llm()

    assert first is second
