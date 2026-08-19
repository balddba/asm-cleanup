"""Simplified command-line entrypoint for web server execution, CLI scans, and migration management.

Enables starting the FastAPI app, triggering target discovery runs directly in the terminal,
and executing database migrations.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from asm_cleanup.logging_config import configure_logging


def _require_web_extra(feature: str) -> None:
    """Exit with a clear message when optional web dependencies are missing.

    Args:
        feature (str): Feature name shown in the error (e.g. web, run, db).
    """
    try:
        import alembic  # noqa: F401
        import fastapi  # noqa: F401
        import jwt  # noqa: F401
        import keyring  # noqa: F401
        import sqlalchemy  # noqa: F401
        import uvicorn  # noqa: F401
        from keyrings.cryptfile.cryptfile import CryptFileKeyring  # noqa: F401
    except ImportError:
        sys.stderr.write(
            f"Error: '{feature}' requires the web optional dependencies.\n"
            "Install with: uv sync --extra web\n"
        )
        sys.exit(1)


def cmd_web(args: argparse.Namespace) -> int:
    """Start the FastAPI uvicorn web server.

    Args:
        args (argparse.Namespace): Parsed command line arguments.

    Returns:
        int: Exit code.
    """
    _require_web_extra("web")
    import uvicorn

    from asm_cleanup.auth import AuthSettings
    from asm_cleanup.auth.ssh_key_store import ssh_key_store_from_env

    try:
        AuthSettings.from_env()
        ssh_key_store_from_env()
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        sys.stderr.write(
            "Set ASM_CLEANUP_PASSWORD, ASM_CLEANUP_JWT_SECRET, and "
            "ASM_CLEANUP_KEYRING_KEY "
            "(run ./scripts/setup_env.sh, or see .env.example).\n"
        )
        return 1

    configure_logging(debug=bool(getattr(args, "debug", False)))
    logger.info("starting web server on host={} port={}", args.host, args.port)
    uvicorn.run(
        "asm_cleanup.web:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run an automated ASM discovery scan synchronously on a target.

    Args:
        args (argparse.Namespace): Parsed command line arguments.

    Returns:
        int: Exit code (0 for success, 1 on scan failure).
    """
    _require_web_extra("run")
    from asm_cleanup.auth.ssh_key_store import ssh_key_store_from_env
    from asm_cleanup.db import DbManager, Scan, Target
    from asm_cleanup.discovery import TargetDiscoveryRunner
    from asm_cleanup.sql.move_sql_emitter import format_generated_sql_for_display

    try:
        ssh_key_store_from_env()
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        sys.stderr.write(
            "Set ASM_CLEANUP_KEYRING_KEY (run ./scripts/setup_env.sh, or see .env.example).\n"
        )
        return 1

    configure_logging()
    db_manager = DbManager()
    db_manager.run_migrations()

    with db_manager.session() as session:
        target = session.query(Target).filter(Target.name == args.target_name).first()
        if not target:
            sys.stderr.write(
                f"Error: Connection target profile '{args.target_name}' not found.\n"
            )
            sys.stderr.write("Please configure targets in the Web UI first.\n")
            return 1

        logger.info(
            "running synchronous CLI discovery scan for target={!r}", target.name
        )

        scan = Scan(target_id=target.id, status="pending")
        session.add(scan)
        session.commit()
        session.refresh(scan)

        runner = TargetDiscoveryRunner(session, target, scan)
        runner.run()

        session.refresh(scan)
        if scan.status == "completed":
            print("============================================================")
            print(" ASM Alias Automated Scan Report")
            print("============================================================")
            print(f" Target Profile  : {target.name}")
            print(" Status          : SUCCESS")
            print(f" Grid Home       : {scan.grid_home}")
            if scan.databases:
                import json

                try:
                    db_meta = json.loads(scan.databases)
                    for db_name, info in db_meta.items():
                        pdb_count = info.get("pdb_count", 0)
                        print(f" CDB {db_name.upper()} PDBs  : {pdb_count}")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    pass
            print("============================================================")
            print(" Generated MOVE SQL:")
            print("------------------------------------------------------------")
            print(format_generated_sql_for_display(scan.generated_sql))
            print("============================================================")
            return 0
        sys.stderr.write(f"Error: Discovery scan failed: {scan.error_message}\n")
        return 1


def cmd_db(args: argparse.Namespace) -> int:
    """Manage database migrations and demo database rebuilds.

    Args:
        args (argparse.Namespace): Parsed command line arguments.

    Returns:
        int: Exit code.
    """
    _require_web_extra("db")
    from asm_cleanup.db import (
        DEFAULT_DEMO_DB_PATH,
        DbManager,
        ProductionDatabaseError,
        build_demo_database,
    )

    configure_logging()
    if args.db_action == "upgrade":
        db_manager = DbManager()
        db_manager.run_migrations()
        return 0
    if args.db_action == "build-demo":
        output = Path(args.output) if args.output else DEFAULT_DEMO_DB_PATH
        try:
            dest = build_demo_database(output)
        except ProductionDatabaseError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return 1
        print(f"Wrote demo database: {dest}")
        print("Use with: docker compose up --build web-demo")
        return 0
    sys.stderr.write(
        f"Error: Unknown db action '{args.db_action}'. "
        "Supported actions: upgrade, build-demo\n"
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    """Construct command line argument parser with simplified subcommands.

    Returns:
        argparse.ArgumentParser: Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Oracle ASM Alias Clean-up, Database Storage, and Migration automation tool."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    web_p = sub.add_parser("web", help="Start the interactive web dashboard")
    web_p.add_argument("--host", default="127.0.0.1", help="Server bind address")
    web_p.add_argument("--port", type=int, default=8000, help="Server bind port")
    web_p.add_argument("--reload", action="store_true", help="Enable hot reloading")
    web_p.set_defaults(func=cmd_web)

    run_p = sub.add_parser("run", help="Run a synchronous discovery scan on a target")
    run_p.add_argument(
        "target_name", help="Name of the target connection profile to scan"
    )
    run_p.set_defaults(func=cmd_run)

    db_p = sub.add_parser("db", help="Run database schema migration tools")
    db_p.add_argument(
        "db_action",
        choices=["upgrade", "build-demo"],
        help="Database migration or demo rebuild action",
    )
    db_p.add_argument(
        "--output",
        default=None,
        help="Output path for build-demo (default: docs/demo/asm_cleanup_demo.db)",
    )
    db_p.set_defaults(func=cmd_db)

    return parser


def main(argv: list[str] | None = None) -> None:
    """Command-line entrypoint dispatcher.

    Args:
        argv (list[str] | None): Optional argument overrides.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code = args.func(args)
        sys.exit(code)
    except Exception as exc:  # noqa: BLE001 (CLI entrypoint: print clean error message and exit)
        sys.stderr.write(f"Fatal Error: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
