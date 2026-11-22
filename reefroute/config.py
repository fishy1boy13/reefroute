import re
import tomllib
from pathlib import Path


def validate(cfg):
    providers = cfg.get("providers", {})
    callers = cfg.get("callers", {})
    routes = cfg.get("routes", {})
    if not providers or not callers or not routes:
        raise ValueError("providers, callers and routes are required")
    for name, spec in providers.items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise ValueError("invalid provider name")
        if spec.get("kind") not in {"echo", "compatible"}:
            raise ValueError("unsupported provider kind")
        if type(spec.get("request_microcredits")) is not int or spec["request_microcredits"] < 0:
            raise ValueError("each provider needs a nonnegative integer request_microcredits")
        if spec["kind"] == "compatible" and (not spec.get("base_url") or not spec.get("key_env")):
            raise ValueError("compatible providers require base_url and key_env")
    for spec in callers.values():
        if not isinstance(spec.get("token_env"), str) or not spec["token_env"]:
            raise ValueError("caller token_env required")
        if type(spec.get("daily_cap_microcredits")) is not int or spec["daily_cap_microcredits"] < 0:
            raise ValueError("caller daily cap must be a nonnegative integer")
        if not isinstance(spec.get("providers"), list) or not spec["providers"] or any(p not in providers for p in spec["providers"]):
            raise ValueError("caller providers must name configured providers")
    for alias, route in routes.items():
        if not isinstance(route, dict) or route.get("provider") not in providers or not isinstance(route.get("model"), str) or not route["model"]:
            raise ValueError("route must name a provider and model")
        if not alias:
            raise ValueError("route alias is empty")
    if cfg.get("alerts", {}).get("kind", "stdout") not in {"stdout", "webhook"}:
        raise ValueError("unsupported notifier")
    return cfg


def load(path):
    path = Path(path).resolve()
    with path.open("rb") as handle:
        cfg = validate(tomllib.load(handle))
    database = Path(cfg.get("database", "usage.sqlite"))
    cfg["database"] = str(path.parent / database)
    return cfg
