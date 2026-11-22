import argparse
import asyncio
import json
from aiohttp import web
from .app import STATE, create_app, discover
from .config import load
from .demo import run
from .store import Store


async def catalog(cfg):
    app = create_app(cfg)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        return await discover(app[STATE])
    finally:
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description="Text routing with local microcredit budgets")
    parser.add_argument("--config", default="reefroute.example.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    commands.add_parser("demo")
    commands.add_parser("report")
    commands.add_parser("ledger")
    commands.add_parser("discover")
    args = parser.parse_args()
    if args.command == "demo":
        asyncio.run(run())
        return
    cfg = load(args.config)
    if args.command == "serve":
        web.run_app(create_app(cfg), host=args.host, port=args.port, access_log=None)
    elif args.command == "discover":
        print(json.dumps(asyncio.run(catalog(cfg)), indent=2))
    else:
        store = Store(cfg["database"])
        try:
            print(json.dumps(store.report() if args.command == "report" else store.ledger(), indent=2))
        finally:
            store.close()


if __name__ == "__main__":
    main()
