"""Exercise real routing handlers without sockets, persistent files or external APIs."""
import json
import os
import secrets
from contextlib import contextmanager
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from .app import STATE, create_app
from .store import Store


async def invoke(app, method, path, token, body=None):
    request = make_mocked_request(method, path, app=app,
                                  headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    request._read_bytes = json.dumps(body).encode() if body is not None else b""
    match = await app.router.resolve(request)
    match.add_app(app)
    request._match_info = match
    try:
        return await match.handler(request)
    except web.HTTPException as response:
        return response


@contextmanager
def demo_credential():
    name = "REEFROUTE_DEMO_TOKEN"
    previous = os.environ.get(name)
    token = secrets.token_urlsafe(32)
    os.environ[name] = token
    try:
        yield token
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def demo_config():
    return {"providers": {"demo": {"kind": "echo", "request_microcredits": 100}},
            "callers": {"demo": {"token_env": "REEFROUTE_DEMO_TOKEN", "daily_cap_microcredits": 200, "providers": ["demo"]}},
            "routes": {"sample": {"provider": "demo", "model": "echo-text"}}}


async def run():
    store = Store(":memory:")
    with demo_credential() as token:
        app = create_app(demo_config(), store=store)
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            print("Offline demo: fictional costs, in-memory ledger, no sockets")
            for number in range(1, 5):
                response = await invoke(app, "POST", "/v1/chat/completions", token,
                                        {"model": "sample", "messages": [{"role": "user", "content": "hello demo"}], "max_tokens": 32})
                print(f"request {number}: HTTP {response.status}")
            rows = [{k: v for k, v in row.items() if k != "id"} for row in store.ledger()]
            print("ledger:", json.dumps(rows, sort_keys=True))
            print("totals:", json.dumps(store.report(), sort_keys=True))
            print("alert attempts:", store.db.execute("SELECT COUNT(*) FROM alerts").fetchone()[0])
        finally:
            await runner.cleanup()
            store.close()
