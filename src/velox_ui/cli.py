"""Command-line interface.

Import cost is the design constraint here. ``velox --help`` and ``velox version`` must
not pay for FastAPI, SQLAlchemy or a server: every heavy import lives inside the
subcommand that needs it, which is a large part of how the sub-second cold start is met
(ADR-0015 measures it).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from velox_ui import __version__

__all__ = ["main"]

_EPILOG = """
examples:
  velox serve                          start the server on port 8080
  velox serve --port 9000 --reload     start in development mode
  velox config check                   validate the configuration and print it
  velox migrate up                     bring the database schema up to date
  velox bench                          run the performance suite
  velox import openwebui export.json --user alice@example.com
                                        import chats from an Open WebUI export
  velox import mcp-config .mcp.json    import MCP servers from a Claude Code config
"""


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser.

    Returns:
        The parser for the ``velox`` entry point.
    """
    parser = argparse.ArgumentParser(
        prog="velox",
        description="velox-ui: a fast, self-hosted frontend for local and remote LLMs.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"velox-ui {__version__}")
    parser.add_argument(
        "-c", "--config", metavar="PATH", help="Path to velox.toml. Overrides discovery."
    )
    subcommands = parser.add_subparsers(dest="command", metavar="<command>")

    serve = subcommands.add_parser("serve", help="Run the HTTP server.")
    serve.add_argument("--host", help="Bind address. Overrides configuration.")
    serve.add_argument("--port", type=int, help="Bind port. Overrides configuration.")
    serve.add_argument(
        "--server",
        choices=("granian", "uvicorn"),
        help="ASGI server to use. Overrides configuration.",
    )
    serve.add_argument(
        "--reload", action="store_true", help="Restart on source changes. Development only."
    )
    serve.add_argument("--log-format", choices=("json", "console"), help="Log output format.")

    config = subcommands.add_parser("config", help="Inspect configuration.")
    config_actions = config.add_subparsers(dest="config_command", metavar="<action>")
    config_actions.add_parser("check", help="Validate and print the effective configuration.")

    migrate = subcommands.add_parser("migrate", help="Manage the database schema.")
    migrate_actions = migrate.add_subparsers(dest="migrate_command", metavar="<action>")
    migrate_actions.add_parser("up", help="Apply every pending migration.")
    migrate_actions.add_parser("current", help="Show the revision the database is at.")

    bench = subcommands.add_parser("bench", help="Run the performance benchmark suite.")
    bench.add_argument("--case", action="append", help="Run only the named case. Repeatable.")
    bench.add_argument("--json", metavar="PATH", help="Write results as JSON to this path.")

    import_cmd = subcommands.add_parser(
        "import", help="Import chats or MCP servers from another interface."
    )
    import_actions = import_cmd.add_subparsers(dest="import_command", metavar="<source>")
    openwebui = import_actions.add_parser("openwebui", help="Import an Open WebUI chat export.")
    openwebui.add_argument("path", help="Path to the export JSON file.")
    openwebui.add_argument(
        "--user", required=True, metavar="EMAIL", help="Account the chats are imported into."
    )
    openwebui.add_argument(
        "--folders",
        metavar="PATH",
        help="Open WebUI folders export JSON, to preserve folder names.",
    )
    mcp_config = import_actions.add_parser(
        "mcp-config", help="Import servers from a Claude Code mcpServers config."
    )
    mcp_config.add_argument("path", help="Path to the mcpServers JSON file.")
    mcp_config.add_argument(
        "--owner",
        metavar="EMAIL",
        help="Account the servers belong to. Omit for instance-wide (ownerless) servers.",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI.

    Args:
        argv: Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        A process exit status.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    handlers = {
        "serve": _serve,
        "config": _config,
        "migrate": _migrate,
        "bench": _bench,
        "import": _import,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        return 130


def _load(args: argparse.Namespace) -> object:
    """Load settings, reporting configuration problems without a traceback."""
    from velox_ui.settings import SettingsError, load_settings

    try:
        return load_settings(args.config)
    except SettingsError as exc:
        print(f"velox: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _serve(args: argparse.Namespace) -> int:
    """Start the HTTP server."""
    import msgspec

    from velox_ui.logging import configure_logging
    from velox_ui.settings import LogFormat, ServerKind, Settings

    settings = _load(args)
    assert isinstance(settings, Settings)  # noqa: S101 - narrowing for the type checker

    overrides: dict[str, object] = {}
    if args.host:
        overrides["host"] = args.host
    if args.port:
        overrides["port"] = args.port
    if args.server:
        overrides["server"] = ServerKind(args.server)
    if args.log_format:
        overrides["log_format"] = LogFormat(args.log_format)
    if overrides:
        settings = msgspec.structs.replace(settings, **overrides)

    configure_logging(
        level=settings.log_level, json_output=settings.log_format is LogFormat.JSON
    )

    if settings.server is ServerKind.UVICORN:
        return _serve_uvicorn(settings, reload=args.reload)
    return _serve_granian(settings, reload=args.reload)


def _serve_granian(settings: object, *, reload: bool) -> int:
    """Run under Granian, falling back to uvicorn when it is not installed."""
    from velox_ui.settings import Settings

    assert isinstance(settings, Settings)  # noqa: S101
    try:
        from granian import Granian
        from granian.constants import Interfaces
    except ImportError:
        print(
            "velox: granian is not installed; falling back to uvicorn. "
            "Install granian or set VELOX_SERVER=uvicorn to silence this.",
            file=sys.stderr,
        )
        return _serve_uvicorn(settings, reload=reload)

    _store_settings(settings)
    Granian(
        "velox_ui.app:create_app",
        address=settings.host,
        port=settings.port,
        interface=Interfaces.ASGI,
        workers=settings.workers,
        reload=reload,
        factory=True,
    ).serve()
    return 0


def _serve_uvicorn(settings: object, *, reload: bool) -> int:
    """Run under uvicorn with uvloop and httptools when available."""
    from velox_ui.settings import Settings

    assert isinstance(settings, Settings)  # noqa: S101
    try:
        import uvicorn
    except ImportError:
        print(
            "velox: neither granian nor uvicorn is installed. "
            "Install one of them: pip install 'velox-ui[uvicorn]'",
            file=sys.stderr,
        )
        return 2

    _store_settings(settings)
    uvicorn.run(
        "velox_ui.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=reload,
        workers=settings.workers if not reload else 1,
        log_config=None,
        access_log=False,
    )
    return 0


def _store_settings(settings: object) -> None:
    """Persist the resolved settings for the worker process to pick up.

    Both servers import the application by string so they can reload and fork workers.
    The worker therefore re-reads configuration rather than inheriting the parsed
    object, so command-line overrides are exported to the environment, which is the one
    channel that survives the fork.
    """
    import os

    from velox_ui.settings import Settings

    assert isinstance(settings, Settings)  # noqa: S101
    os.environ["VELOX_HOST"] = settings.host
    os.environ["VELOX_PORT"] = str(settings.port)
    os.environ["VELOX_LOG_FORMAT"] = str(settings.log_format)
    os.environ["VELOX_LOG_LEVEL"] = settings.log_level
    os.environ["VELOX_SECRET_KEY"] = settings.secret_key
    if settings.config_path is not None:
        os.environ["VELOX_CONFIG"] = str(settings.config_path)


def _config(args: argparse.Namespace) -> int:
    """Validate configuration and print it with secrets masked."""
    import msgspec

    from velox_ui.security.crypto import mask_secret
    from velox_ui.settings import Settings

    if args.config_command != "check":
        build_parser().parse_args(["config", "--help"])
        return 2

    settings = _load(args)
    assert isinstance(settings, Settings)  # noqa: S101
    rendered = msgspec.to_builtins(settings, str_keys=True, builtin_types=None, enc_hook=str)
    # Every credential in the configuration is masked here, not only the obvious one:
    # `velox config check` is the command people paste into bug reports.
    rendered["secret_key"] = mask_secret(settings.secret_key)
    if settings.auth.admin_password:
        rendered["auth"]["admin_password"] = mask_secret(settings.auth.admin_password)
    rendered["database_url"] = _mask_url_password(settings.database_url)
    for endpoint, shown in zip(
        settings.providers.endpoints, rendered["providers"]["endpoints"], strict=True
    ):
        if endpoint.api_key:
            shown["api_key"] = mask_secret(endpoint.api_key)
    source = settings.config_path or "defaults and environment only"
    print(f"configuration is valid (source: {source})")
    print(msgspec.json.format(msgspec.json.encode(rendered).decode("utf-8"), indent=2))
    return 0


def _mask_url_password(url: str) -> str:
    """Hide the password in a database URL such as ``postgresql://user:pw@host/db``."""
    scheme, separator, rest = url.partition("://")
    if not separator or "@" not in rest:
        return url
    credentials, _, location = rest.partition("@")
    user, has_password, _ = credentials.partition(":")
    if not has_password:
        return url
    return f"{scheme}://{user}:***@{location}"


def _migrate(args: argparse.Namespace) -> int:
    """Apply or inspect database migrations."""
    import asyncio

    from velox_ui.settings import Settings

    settings = _load(args)
    assert isinstance(settings, Settings)  # noqa: S101

    if args.migrate_command == "up":
        from velox_ui.db.migrate import upgrade_to_head

        asyncio.run(upgrade_to_head(settings.database_url))
        print("database is up to date")
        return 0

    if args.migrate_command == "current":
        from velox_ui.db.engine import Database
        from velox_ui.db.migrate import current_revision, head_revision

        async def _show() -> None:
            database = Database(settings.database_url)
            try:
                at = await current_revision(database.engine)
            finally:
                await database.dispose()
            head = head_revision()
            print(f"current: {at or 'none'}")
            print(f"head:    {head or 'none'}")
            print("status:  up to date" if at == head else "status:  migrations pending")

        asyncio.run(_show())
        return 0

    build_parser().parse_args(["migrate", "--help"])
    return 2


def _bench(args: argparse.Namespace) -> int:
    """Run the benchmark suite."""
    try:
        from bench.harness import run_cli
    except ImportError:
        print(
            "velox: the benchmark suite is only available from a source checkout.",
            file=sys.stderr,
        )
        return 2
    return run_cli(cases=args.case, json_path=args.json)


def _import(args: argparse.Namespace) -> int:
    """Import chats or MCP servers from another interface."""
    if args.import_command == "mcp-config":
        return _import_mcp_config(args)
    if args.import_command != "openwebui":
        build_parser().parse_args(["import", "--help"])
        return 2

    import asyncio
    import json

    from velox_ui.db.engine import Database
    from velox_ui.db.repositories.users import UserRepository
    from velox_ui.services.importers.openwebui import import_export, parse_export
    from velox_ui.settings import Settings

    settings = _load(args)
    assert isinstance(settings, Settings)  # noqa: S101 - narrowing for the type checker

    try:
        with open(args.path, "rb") as handle:
            raw = handle.read()
        entries = parse_export(raw)
    except (OSError, ValueError) as exc:
        print(f"velox: {exc}", file=sys.stderr)
        return 2

    folder_names: dict[str, str] | None = None
    if args.folders:
        try:
            with open(args.folders, "rb") as handle:
                folder_entries = json.loads(handle.read())
            folder_names = {
                str(f["id"]): str(f["name"])
                for f in folder_entries
                if isinstance(f, dict) and "id" in f and "name" in f
            }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"velox: could not read --folders: {exc}", file=sys.stderr)
            return 2

    async def _run() -> int:
        database = Database(settings.database_url)
        try:
            async with database.session() as session:
                user = await UserRepository(session).by_email(args.user)
                user_id = user.id if user is not None else None
            if user_id is None:
                print(f"velox: no account with email {args.user!r}", file=sys.stderr)
                return 1

            async with database.write() as session:
                report = await import_export(
                    session, user_id=user_id, entries=entries, folder_names=folder_names
                )
        finally:
            await database.dispose()

        print(f"imported {report.imported} chat(s)")
        for skip in report.skipped:
            print(f"  skipped {skip.source_id} ({skip.title!r}): {skip.reason}")
        return 0

    return asyncio.run(_run())


def _import_mcp_config(args: argparse.Namespace) -> int:
    """Import MCP servers from a Claude Code ``mcpServers`` config."""
    import asyncio

    from velox_ui.db.engine import Database
    from velox_ui.db.repositories.mcp_servers import McpServerRepository
    from velox_ui.db.repositories.users import UserRepository
    from velox_ui.services.mcp_import import McpImportError, parse_claude_mcp_config
    from velox_ui.settings import Settings

    settings = _load(args)
    assert isinstance(settings, Settings)  # noqa: S101 - narrowing for the type checker

    try:
        with open(args.path, "rb") as handle:
            raw = handle.read()
        imported = parse_claude_mcp_config(raw)
    except (OSError, McpImportError) as exc:
        print(f"velox: {exc}", file=sys.stderr)
        return 2

    async def _run() -> int:
        database = Database(settings.database_url)
        try:
            owner_id: str | None = None
            if args.owner:
                async with database.session() as session:
                    user = await UserRepository(session).by_email(args.owner)
                if user is None:
                    print(f"velox: no account with email {args.owner!r}", file=sys.stderr)
                    return 1
                owner_id = user.id

            async with database.write() as session:
                repository = McpServerRepository(session)
                for server in imported:
                    await repository.create(
                        owner_id=owner_id,
                        name=server.name,
                        transport=server.transport,
                        config=server.config,
                        approval="always",
                        enabled=True,
                    )
        finally:
            await database.dispose()

        print(f"imported {len(imported)} MCP server(s)")
        for server in imported:
            if server.dropped_cwd:
                print(f"  {server.name}: 'cwd' has no equivalent field and was dropped")
        return 0

    return asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
