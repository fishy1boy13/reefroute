"""Authenticated, non-streaming text routes with fixed dispatch accounting."""
import asyncio
import hmac
import os
from aiohttp import ClientSession, ClientTimeout, web
from .alerts import Stdout, Webhook, notify
from .config import validate
from .providers import Compatible, Echo, ProviderError
from .store import Store

STATE = web.AppKey("state", dict)


def authenticate(request, cfg):
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise web.HTTPUnauthorized(text="credential required")
    token = header[7:]
    for name, spec in cfg["callers"].items():
        expected = os.environ.get(spec["token_env"], "")
        if expected and hmac.compare_digest(token.encode(), expected.encode()):
            return name, spec
    raise web.HTTPUnauthorized(text="invalid credential")


def body_valid(body):
    if not isinstance(body, dict) or set(body) - {"model", "messages", "max_tokens", "stream"}:
        return False
    messages = body.get("messages")
    return (isinstance(body.get("model"), str) and type(body.get("max_tokens")) is int
            and 0 < body["max_tokens"] <= 4096 and body.get("stream", False) is False
            and isinstance(messages, list) and 0 < len(messages) <= 100
            and all(isinstance(m, dict) and set(m) == {"role", "content"}
                    and isinstance(m["role"], str) and m["role"] in {"system", "user", "assistant"} and isinstance(m["content"], str) for m in messages))


async def complete(request):
    state = request.app[STATE]
    cfg, store = state["cfg"], state["store"]
    caller, policy = authenticate(request, cfg)
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        raise web.HTTPBadRequest(text="invalid JSON") from None
    if not body_valid(body):
        raise web.HTTPBadRequest(text="requires text messages, model, max_tokens; streaming and extra fields unsupported")
    route = cfg["routes"].get(body["model"])
    if route is None:
        raise web.HTTPNotFound(text="unknown model alias")
    provider = route["provider"]
    if provider not in policy["providers"]:
        raise web.HTTPForbidden(text="provider not allowed")
    cost = cfg["providers"][provider]["request_microcredits"]
    rid = store.reserve(caller, policy["daily_cap_microcredits"], cost, provider, route["model"])
    if rid is None:
        await notify(store, state["notifier"], "cap:" + caller, "daily dispatch budget rejected a request")
        raise web.HTTPTooManyRequests(text="daily cap reached")
    body = dict(body, model=route["model"])
    try:
        result = await state["providers"][provider].complete(body)
        store.finish(rid, "ok", result.get("usage"))
        return web.json_response(result)
    except asyncio.CancelledError:
        store.finish(rid, "cancelled")
        raise
    except Exception:
        store.finish(rid, "failed")
        await notify(store, state["notifier"], "provider:" + provider, "provider request failed")
        raise web.HTTPBadGateway(text="provider request failed") from None


async def models(request):
    state = request.app[STATE]
    _, policy = authenticate(request, state["cfg"])
    return web.json_response({"data": [{"id": alias} for alias, route in state["cfg"]["routes"].items()
                                       if route["provider"] in policy["providers"]]})


async def discover(state):
    result = {}
    for name, provider in state["providers"].items():
        try:
            result[name] = state["store"].catalog(name, await provider.models())
            if any(result[name].values()):
                await notify(state["store"], state["notifier"], "catalog:" + name, "provider catalog changed")
        except ProviderError:
            result[name] = {"error": "catalog unavailable"}
            await notify(state["store"], state["notifier"], "catalog-error:" + name, "provider catalog unavailable")
    return result


def create_app(cfg, *, store=None, adapters=None, notifier=None):
    validate(cfg)
    app = web.Application(client_max_size=64 * 1024)
    app[STATE] = {"cfg": cfg}

    async def lifecycle(app):
        async with ClientSession(timeout=ClientTimeout(total=20), trust_env=False) as http:
            own_store = store is None
            ledger = store if store is not None else Store(cfg.get("database", "usage.sqlite"))
            try:
                providers = adapters if adapters is not None else {
                    name: Echo() if spec["kind"] == "echo" else Compatible(spec, http)
                    for name, spec in cfg["providers"].items()}
                alerts = cfg.get("alerts", {})
                sink = notifier if notifier is not None else (Stdout() if alerts.get("kind", "stdout") == "stdout"
                       else Webhook(http, alerts["url_env"]))
                app[STATE].update(store=ledger, providers=providers, notifier=sink)
                yield
            finally:
                if own_store:
                    ledger.close()
    app.cleanup_ctx.append(lifecycle)
    app.router.add_post("/v1/chat/completions", complete)
    app.router.add_get("/v1/models", models)
    return app
