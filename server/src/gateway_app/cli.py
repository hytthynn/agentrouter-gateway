"""CLI: `agw serve` / `agw migrate` / `agw --version`."""

import argparse
import sys
from pathlib import Path


def main() -> int:
    from gateway_app import __version__

    parser = argparse.ArgumentParser(
        prog="agw", description="AgentRouter Gateway — локальный шлюз к agentrouter.org"
    )
    parser.add_argument("--version", action="version", version=f"agw {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="запустить сервер (API + шлюз + UI + планировщик)")
    p_serve.add_argument("--host", default=None, help="по умолчанию 127.0.0.1 (§13.1)")
    p_serve.add_argument("--port", type=int, default=None, help="по умолчанию 8787")
    p_serve.add_argument("--reload", action="store_true", help="авто-перезагрузка (dev)")

    sub.add_parser("migrate", help="применить миграции Alembic")

    args = parser.parse_args()

    if args.command == "serve":
        from gateway_app.core.config import get_config

        cfg = get_config()
        host = args.host or cfg.host
        port = args.port or cfg.port
        import uvicorn

        uvicorn.run(
            "gateway_app.main:app",
            host=host,
            port=port,
            reload=args.reload,
            log_level=cfg.log_level.lower(),
        )
        return 0

    if args.command == "migrate":
        from alembic import command
        from alembic.config import Config

        from gateway_app.core.config import get_config

        cfg = get_config()
        cfg.ensure_dirs()
        ini = Path(__file__).resolve().parents[1] / "alembic.ini"
        alembic_cfg = Config(str(ini))
        alembic_cfg.set_main_option("sqlalchemy.url", cfg.db_url_sync)
        command.upgrade(alembic_cfg, "head")
        print(f"Миграции применены: {cfg.db_path}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
