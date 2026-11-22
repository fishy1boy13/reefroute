"""Offline echo and a URL-configured compatible JSON API. No embedded endpoints."""
import os
from urllib.parse import urlsplit


class ProviderError(Exception):
    pass


class Echo:
    async def models(self):
        return ["echo-text"]

    async def complete(self, body):
        text = body["messages"][-1]["content"][:body["max_tokens"]]
        return {"model": body["model"], "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": sum(len(m["content"].split()) for m in body["messages"]), "completion_tokens": len(text.split())}}


class Compatible:
    def __init__(self, spec, http):
        self.base = spec["base_url"].rstrip("/")
        url = urlsplit(self.base)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("base_url must be an HTTP(S) URL without credentials, query or fragment")
        key = os.environ.get(spec.get("key_env", ""))
        if not key:
            raise ValueError("configured provider credential environment variable is missing")
        self.headers = {"Authorization": "Bearer " + key}
        self.http = http

    async def request(self, method, path, body=None):
        try:
            async with self.http.request(method, self.base + path, headers=self.headers,
                                         json=body, allow_redirects=False) as response:
                if response.status != 200:
                    raise ProviderError("upstream request failed")
                # Bound buffered JSON. No response bodies or URLs enter errors/logs.
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > 2 * 1024 * 1024:
                        raise ProviderError("upstream response too large")
                import json
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ProviderError("invalid upstream response")
                return result
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("upstream transport or JSON failure") from None

    async def models(self):
        raw = await self.request("GET", "/models")
        items = raw.get("data")
        if not isinstance(items, list):
            raise ProviderError("invalid model catalog")
        return sorted({m["id"] for m in items if isinstance(m, dict) and isinstance(m.get("id"), str)})

    async def complete(self, body):
        result = await self.request("POST", "/chat/completions", body)
        if not isinstance(result.get("choices"), list) or not isinstance(result.get("usage", {}), dict):
            raise ProviderError("invalid completion")
        return result
