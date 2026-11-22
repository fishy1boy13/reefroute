# ReefRoute

A small Python text API router built around aiohttp and SQLite. It demonstrates authenticated routing, per-credential spending caps, provider discovery, persistent alert deduplication and CLI usage reports. The sample is self-contained and uses fictional providers and costs.

Requires Python 3.11 or newer. Install in a virtual environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
reefroute demo
```

The demo runs offline, using the real aiohttp route resolver and handlers with in-process request fixtures. It opens no sockets and writes no database file. Requests 1 and 2 return HTTP 200. Requests 3 and 4 return HTTP 429. The ledger shows two dispatches at 100 microcredits each, a total of 200, and one alert attempt despite two rejected requests. Its temporary credential is generated internally and never printed.

To run the HTTP server:

```sh
cp reefroute.example.toml reefroute.toml
export REEFROUTE_DEMO_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
reefroute --config reefroute.toml serve
```

In another terminal, with the same credential environment, send a request:

```sh
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer $REEFROUTE_DEMO_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"model":"sample","messages":[{"role":"user","content":"hello"}],"max_tokens":32}'
```

`GET /v1/models` lists configured aliases allowed by the credential. Both routes require authentication. The server binds to loopback by default. Use TLS at a reverse proxy before exposing it to a network.

Configuration uses TOML. Provider entries select `echo` or `compatible`, with a fixed integer `request_microcredits` charge. Compatible providers require a URL including the API prefix and the name of an environment variable containing their credential. The commented `example.invalid` URL is deliberately unusable. No vendor prices or endpoints are built in. The adapter calls `/models` and `/chat/completions` beneath the configured base URL, with a 20-second timeout and no redirects.

Caller entries name a credential environment variable, a daily cap and allowed providers. Route entries map a public model alias to one provider and upstream model. Changing the environment and restarting the process revokes or rotates credentials. Keep credentials out of TOML and version control. The database path is relative to the configuration file; its parent directory must already exist.

Costs are **fictional local microcredits per dispatch**, not money charged by an external service. SQLite reserves the entire fixed charge in a `BEGIN IMMEDIATE` transaction before dispatch. Successful, failed, interrupted and cancelled dispatches all retain that charge. This prevents concurrent requests from overspending the local daily cap. A crash can leave a `reserved` row; it still counts. Reset occurs by UTC date, without deleting history. Provider token counts are optional reported metadata, not billing inputs. These caps cannot guarantee an upstream currency budget.

Useful commands:

```sh
reefroute --config reefroute.toml discover
reefroute --config reefroute.toml report
reefroute --config reefroute.toml ledger
python -m pytest -q
```

Discovery snapshots model IDs and reports additions/removals since the last run. It does not automatically expose discovered models or import prices. Catalog changes, discovery failures, request failures and cap rejections produce alerts. Stdout is the default notifier. For webhooks, set `alerts.kind = "webhook"` and `alerts.url_env` to the environment variable holding the destination URL. The payload contains only `condition` and `message`. The persistent deduplication key is condition plus UTC date. It provides one delivery **attempt** per day; a failed attempt is not retried. No prompts, completions, credential values or upstream error bodies enter the ledger or alert payloads.

The package is organized around:

- `app.py`: aiohttp lifecycle, authentication, validation and routing.
- `store.py`: SQLite reservations, ledger, summaries, catalog snapshots and alert claims.
- `providers.py`: echo and URL-configured compatible adapters.
- `alerts.py`: pluggable async notification sinks and deduplication.
- `config.py`, `cli.py`, `demo.py`: configuration, operational commands and offline walkthrough.

Portable systemd templates are in `systemd/`. Replace `{{USER}}` and `{{INSTALL_DIR}}` before installation. Create a private `credentials.env` there containing the environment variables referenced by the config. The templates assume a virtual environment and a writable installation directory for SQLite. Installation and service activation are manual.

Limitations: non-streaming text messages only; no tools, attachments, images, retries, automatic fallback, balance APIs or distributed accounting. Request bodies are limited to 64 KiB and buffered upstream JSON to 2 MiB. Only configured aliases are accepted. SQLite is intended for a single host. Credentials are compared against environment values, with no external identity provider or credential management UI. The echo provider truncates characters using `max_tokens`; its token usage is a word count, not a tokenizer. Tests use fixtures and in-memory adapters; real compatible APIs, webhooks and systemd deployment require operator verification.

License: AGPL-3.0-only. See [LICENSE](LICENSE).
