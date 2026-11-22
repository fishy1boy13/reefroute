import asyncio
import json
from unittest.mock import AsyncMock
import pytest
from aiohttp import web
from reefroute.alerts import Stdout, Webhook, notify
from reefroute.app import STATE, create_app, discover
from reefroute.config import load, validate
from reefroute.demo import demo_config, invoke, run
from reefroute.providers import Compatible, Echo, ProviderError
from reefroute.store import Store


def execute(coro):
    return asyncio.run(coro)


@pytest.fixture
def store():
    value = Store(":memory:")
    yield value
    value.close()


@pytest.fixture
def cfg(monkeypatch):
    monkeypatch.setenv("REEFROUTE_DEMO_TOKEN", "fixture-credential")
    return demo_config()


async def request(cfg, store, body=None, *, token="fixture-credential", method="POST", path="/v1/chat/completions", adapters=None, notifier=None):
    app = create_app(cfg, store=store, adapters=adapters, notifier=notifier)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        return await invoke(app, method, path, token, body)
    finally:
        await runner.cleanup()


def body():
    return {"model": "sample", "messages": [{"role": "user", "content": "hello world"}], "max_tokens": 32}


def test_success(cfg, store):
    r = execute(request(cfg, store, body()))
    assert r.status == 200
    assert json.loads(r.text)["choices"][0]["message"]["content"] == "hello world"
    assert store.ledger()[0]["input_tokens"] == 2
    assert store.ledger()[0]["status"] == "ok"


def test_cap_and_dedup(cfg, store):
    sink = AsyncMock()
    for expected in [200, 200, 429, 429]:
        assert execute(request(cfg, store, body(), notifier=sink)).status == expected
    assert store.report()[0]["microcredits"] == 200
    assert len(store.ledger()) == 2
    assert sink.send.await_count == 1


def test_auth(cfg, store):
    assert execute(request(cfg, store, body(), token="wrong")).status == 401
    assert not store.ledger()


def test_missing_env(cfg, store, monkeypatch):
    monkeypatch.delenv("REEFROUTE_DEMO_TOKEN")
    assert execute(request(cfg, store, body())).status == 401


@pytest.mark.parametrize("change", [
    {"stream": True}, {"max_tokens": True}, {"max_tokens": 0}, {"max_tokens": 4097},
    {"messages": []}, {"messages": [{"role": "user", "content": []}]},
    {"tools": []}, {"model": 123}, {"messages": [{"role": [], "content": "hi"}]}, {"messages": [{"role": "other", "content": "hi"}]},
])
def test_validation(cfg, store, change):
    b = body() | change
    assert execute(request(cfg, store, b)).status == 400
    assert not store.ledger()


def test_unknown_model(cfg, store):
    assert execute(request(cfg, store, body() | {"model": "missing"})).status == 404


def test_policy(cfg, store):
    cfg["providers"]["other"] = {"kind": "echo", "request_microcredits": 0}
    cfg["routes"]["sample"]["provider"] = "other"
    assert execute(request(cfg, store, body())).status == 403
    r = execute(request(cfg, store, method="GET", path="/v1/models"))
    assert json.loads(r.text) == {"data": []}


def test_model_alias(cfg, store):
    r = execute(request(cfg, store, method="GET", path="/v1/models"))
    assert json.loads(r.text) == {"data": [{"id": "sample"}]}


def test_failure_retains_charge(cfg, store):
    provider = AsyncMock()
    provider.complete.side_effect = ProviderError("private response")
    r = execute(request(cfg, store, body(), adapters={"demo": provider}))
    assert r.status == 502 and "private" not in r.text
    assert store.ledger()[0]["status"] == "failed"
    assert store.report()[0]["microcredits"] == 100


def test_cancel_retains_charge(cfg, store):
    provider = AsyncMock()
    provider.complete.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        execute(request(cfg, store, body(), adapters={"demo": provider}))
    assert store.ledger()[0]["status"] == "cancelled"
    assert store.report()[0]["microcredits"] == 100


def test_persistent_cap_and_alert(tmp_path):
    path = tmp_path / "ledger.sqlite"
    first, second = Store(path), Store(path)
    try:
        assert first.reserve("sample", 100, 100, "demo", "echo-text")
        assert second.reserve("sample", 100, 1, "demo", "echo-text") is None
        assert first.claim_alert("limit")
        assert not second.claim_alert("limit")
    finally:
        first.close()
        second.close()


def test_day_reset(store, monkeypatch):
    monkeypatch.setattr("reefroute.store.day", lambda: "2000-01-01")
    assert store.reserve("sample", 100, 100, "demo", "echo-text")
    assert store.claim_alert("limit")
    monkeypatch.setattr("reefroute.store.day", lambda: "2000-01-02")
    assert store.reserve("sample", 100, 100, "demo", "echo-text")
    assert store.claim_alert("limit")


@pytest.mark.parametrize("cap,cost", [(-1, 1), (1, -1), (True, 1), (1, float("nan"))])
def test_bad_accounting(store, cap, cost):
    with pytest.raises(ValueError):
        store.reserve("sample", cap, cost, "demo", "echo-text")


def test_discovery(cfg, store):
    async def scenario():
        app = create_app(cfg, store=store, notifier=AsyncMock())
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            assert await discover(app[STATE]) == {"demo": {"added": ["echo-text"], "removed": []}}
            assert await discover(app[STATE]) == {"demo": {"added": [], "removed": []}}
            app[STATE]["providers"]["demo"].models = AsyncMock(return_value=[])
            assert await discover(app[STATE]) == {"demo": {"added": [], "removed": ["echo-text"]}}
        finally:
            await runner.cleanup()
    execute(scenario())


def test_discovery_error(cfg, store):
    async def scenario():
        adapter = AsyncMock()
        adapter.models.side_effect = ProviderError("private")
        state = {"providers": {"demo": adapter}, "store": store, "notifier": AsyncMock()}
        assert await discover(state) == {"demo": {"error": "catalog unavailable"}}
        assert state["notifier"].send.await_count == 1
    execute(scenario())


def test_failed_alert_dedup(store):
    sink = AsyncMock()
    sink.send.side_effect = RuntimeError("private")
    assert not execute(notify(store, sink, "limit", "cap"))
    assert not execute(notify(store, sink, "limit", "cap"))
    assert sink.send.await_count == 1


def test_config_example():
    cfg = load("reefroute.example.toml")
    assert cfg["providers"]["demo"]["kind"] == "echo"


@pytest.mark.parametrize("mutation", [
    lambda c: c["providers"]["demo"].update(kind="missing"),
    lambda c: c["providers"]["demo"].update(request_microcredits=-1),
    lambda c: c["callers"]["demo"].update(daily_cap_microcredits=True),
    lambda c: c["callers"]["demo"].update(providers=["unknown"]),
    lambda c: c["routes"]["sample"].update(provider="unknown"),
])
def test_config_rejects(cfg, mutation):
    mutation(cfg)
    with pytest.raises(ValueError):
        validate(cfg)


class Response:
    def __init__(self, payload, status=200):
        self.status = status
        self.raw = json.dumps(payload).encode()
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        yield self.raw


class Http:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response

    def post(self, *args, **kwargs):
        return self.request(*args, **kwargs)


def test_compatible(monkeypatch):
    monkeypatch.setenv("SAMPLE_PROVIDER_CREDENTIAL", "fixture-provider")
    http = Http(Response({"data": [{"id": "sample-text"}, {"bad": True}]}))
    adapter = Compatible({"base_url": "https://api.example.invalid/v1", "key_env": "SAMPLE_PROVIDER_CREDENTIAL"}, http)
    assert execute(adapter.models()) == ["sample-text"]
    assert http.calls[0][0] == ("GET", "https://api.example.invalid/v1/models")
    assert http.calls[0][1]["allow_redirects"] is False
    http.response = Response({"choices": [], "usage": {"prompt_tokens": 1}})
    assert execute(adapter.complete(body()))["usage"]["prompt_tokens"] == 1


def test_compatible_error_sanitized(monkeypatch):
    monkeypatch.setenv("SAMPLE_PROVIDER_CREDENTIAL", "fixture-provider")
    adapter = Compatible({"base_url": "https://api.example.invalid/v1", "key_env": "SAMPLE_PROVIDER_CREDENTIAL"}, Http(Response({"error": "private"}, 401)))
    with pytest.raises(ProviderError, match="^upstream request failed$"):
        execute(adapter.models())


def test_webhook(monkeypatch):
    monkeypatch.setenv("SAMPLE_WEBHOOK_URL", "https://hooks.example.invalid/notify")
    http = Http(Response({}, 204))
    execute(Webhook(http, "SAMPLE_WEBHOOK_URL").send("condition", "message"))
    assert http.calls[0][1]["json"] == {"condition": "condition", "message": "message"}
    assert http.calls[0][1]["allow_redirects"] is False


def test_demo(capsys):
    execute(run())
    output = capsys.readouterr().out
    assert output.count("HTTP 200") == 2
    assert output.count("HTTP 429") == 2
    assert output.count('"alert":') == 1
    assert '"microcredits": 200' in output
    assert "alert attempts: 1" in output


def test_concurrent_reservations(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    path = tmp_path / "concurrent.sqlite"
    Store(path).close()
    barrier = Barrier(2)

    def reserve():
        ledger = Store(path)
        try:
            barrier.wait(timeout=5)
            return ledger.reserve("sample", 100, 100, "demo", "echo-text")
        finally:
            ledger.close()

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: reserve(), range(2)))
    assert sum(r is not None for r in results) == 1


def test_cli_reports_and_discovery(tmp_path, monkeypatch, capsys):
    from reefroute.cli import main
    config = tmp_path / "sample.toml"
    config.write_text(open("reefroute.example.toml").read())
    for command in ["discover", "report", "ledger"]:
        monkeypatch.setattr("sys.argv", ["reefroute", "--config", str(config), command])
        main()
        output = capsys.readouterr().out
        if command == "discover":
            assert '"echo-text"' in output
        else:
            assert json.loads(output) == []


@pytest.mark.parametrize("url", ["file:///tmp/sample", "https://user:password@example.invalid", "https://api.example.invalid?secret=placeholder"])
def test_compatible_rejects_unsafe_url(url, monkeypatch):
    monkeypatch.setenv("SAMPLE_PROVIDER_CREDENTIAL", "fixture-provider")
    with pytest.raises(ValueError):
        Compatible({"base_url": url, "key_env": "SAMPLE_PROVIDER_CREDENTIAL"}, Http(Response({})))


@pytest.mark.parametrize("raw", [b"invalid JSON", b"[]", b"x" * (2 * 1024 * 1024 + 1)])
def test_compatible_bad_response(raw, monkeypatch):
    monkeypatch.setenv("SAMPLE_PROVIDER_CREDENTIAL", "fixture-provider")
    response = Response({})
    response.raw = raw
    adapter = Compatible({"base_url": "https://api.example.invalid/v1", "key_env": "SAMPLE_PROVIDER_CREDENTIAL"}, Http(response))
    with pytest.raises(ProviderError):
        execute(adapter.models())
