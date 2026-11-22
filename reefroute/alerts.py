"""One delivery attempt per condition per UTC day, persisted in SQLite."""
import json
import os


class Stdout:
    async def send(self, condition, message):
        print(json.dumps({"alert": condition, "message": message}))


class Webhook:
    def __init__(self, http, url_env):
        self.http = http
        self.url_env = url_env

    async def send(self, condition, message):
        url = os.environ.get(self.url_env)
        if not url:
            raise ValueError("webhook environment variable is missing")
        async with self.http.post(url, json={"condition": condition, "message": message}, allow_redirects=False) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError("webhook delivery failed")


async def notify(store, notifier, condition, message):
    if not store.claim_alert(condition):
        return False
    try:
        await notifier.send(condition, message)
        return True
    except Exception:
        # Do not leak webhook URLs, credentials or provider response bodies.
        return False
