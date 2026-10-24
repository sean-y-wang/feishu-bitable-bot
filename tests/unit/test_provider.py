from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, BadRequestError

from bitable_bot.config.models import ProviderConfig
from bitable_bot.providers.base import LLMError
from bitable_bot.providers.openai_compat import OpenAICompatibleProvider, build_providers

REQ = httpx.Request("POST", "http://x/v1/chat/completions")


def _status(code, cls=APIStatusError):
    return cls("err", response=httpx.Response(code, request=REQ), body=None)


def _ok(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=SimpleNamespace(total_tokens=10)
    )


class FakeClient:
    def __init__(self, results):
        self.results = list(results)
        self.kwargs = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.kwargs.append(kw)
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(results, schema=True):
    cfg = ProviderConfig(base_url="http://x/v1", model="m", supports_json_schema=schema)
    c = FakeClient(results)
    return OpenAICompatibleProvider("p", cfg, client=c, sleep=lambda s: None), c


def test_uses_json_schema_then_degrades():
    p, c = make([_status(400, BadRequestError), _status(400, BadRequestError), _ok("{}")])
    assert p.extract([{"role": "user", "content": "x"}], {"type": "object"}) == "{}"
    fmts = [k.get("response_format", {}).get("type") for k in c.kwargs]
    assert fmts == ["json_schema", "json_object", None]


def test_bad_request_exhausted():
    p, _ = make([_status(400, BadRequestError)], schema=False)
    p._formats = lambda s: [None]
    with pytest.raises(LLMError):
        p.extract([], {})


def test_retry_on_429_and_5xx_then_success():
    p, c = make([_status(429), APIConnectionError(request=REQ), _ok('{"a":1}')], schema=False)
    assert p.extract([], {}) == '{"a":1}'
    assert c.kwargs[0]["response_format"] == {"type": "json_object"}


def test_retry_exhausted_and_non_retryable():
    p, _ = make([_status(503)] * 3, schema=False)
    with pytest.raises(LLMError):
        p.extract([], {})
    p, _ = make([_status(401)], schema=False)
    with pytest.raises(LLMError):
        p.extract([], {})


def test_build_providers(cfg):
    ps = build_providers(cfg)
    assert set(ps) == {"openai", "qwen", "deepseek", "private"}
    assert ps["openai"].supports_json_schema and ps["qwen"].model == "qwen-plus"
