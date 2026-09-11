"""Configuration: TOML file plus environment variables, validated at startup.

Three rules shape this module:

* **It works with no configuration at all.** Every field has a default that produces
  a running instance with a SQLite database under the data directory.
* **Environment always wins.** A key in ``velox.toml`` is overridden by the matching
  ``VELOX_*`` variable, so a container can be reconfigured without rebuilding an image.
* **Errors are readable.** An invalid value names the setting, the file it came from
  and what was expected, instead of a msgspec traceback.

The environment variable for any setting is ``VELOX_`` followed by its path, joined by
underscores and uppercased: ``port`` is ``VELOX_PORT``, ``db.url`` is ``VELOX_DB_URL``,
``auth.access_token_ttl_s`` is ``VELOX_AUTH_ACCESS_TOKEN_TTL_S``.
"""

from __future__ import annotations

import os
import secrets
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any

import msgspec

__all__ = [
    "AuthSettings",
    "DatabaseSettings",
    "LogFormat",
    "ServerKind",
    "Settings",
    "SettingsError",
    "load_settings",
]

ENV_PREFIX = "VELOX_"
CONFIG_FILENAME = "velox.toml"


class SettingsError(Exception):
    """Raised when configuration cannot be loaded or is invalid."""


class ServerKind(StrEnum):
    """Supported ASGI servers (ADR-0002)."""

    GRANIAN = "granian"
    UVICORN = "uvicorn"


class LogFormat(StrEnum):
    """Log output format."""

    JSON = "json"
    CONSOLE = "console"


class DatabaseSettings(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Database connection settings.

    Attributes:
        url: SQLAlchemy async URL. Empty means "derive a SQLite path under the data
            directory", which is the zero-config default.
        echo: Log every statement. Development only; it is expensive.
        pool_size: Reader pool size. SQLite writes are serialized through a single
            connection regardless of this value (ADR-0003).
        auto_migrate: Run pending Alembic migrations at startup.
    """

    url: str = ""
    echo: bool = False
    pool_size: int = 5
    auto_migrate: bool = True


class AuthSettings(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Authentication and session settings.

    Attributes:
        enabled: When false, every request runs as a built-in local user. This is the
            single-user desktop case, and it exists so that a private instance is not
            forced through a login screen.
        open_registration: Allow anyone to create an account.
        access_token_ttl_s: Lifetime of the short-lived access JWT.
        refresh_token_ttl_s: Lifetime of a refresh token family.
        admin_email: Bootstrap administrator, created on first startup if absent.
        admin_password: Bootstrap password. Leave empty to have one generated and
            printed once to the log.
    """

    enabled: bool = True
    open_registration: bool = False
    access_token_ttl_s: int = 900
    refresh_token_ttl_s: int = 2_592_000
    admin_email: str = "admin@velox.local"
    admin_password: str = ""


class MetricsSettings(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Prometheus exposition settings.

    Attributes:
        enabled: Serve ``/metrics``.
        require_auth: Require an admin token to scrape. Off by default because the
            endpoint is normally reachable only from inside the deployment network.
    """

    enabled: bool = True
    require_auth: bool = False


class Settings(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Top-level configuration.

    Attributes:
        host: Bind address.
        port: Bind port.
        data_dir: Writable directory for the database, uploads and the secret key.
        server: Which ASGI server ``velox serve`` launches.
        workers: Worker processes. One by default: more would fragment the in-process
            caches and the SQLite writer (ADR-0002).
        log_level: Root log level.
        log_format: ``json`` for deployments, ``console`` for a terminal.
        secret_key: Key for encrypting provider credentials and signing tokens. Empty
            means "read or create ``secret.key`` in the data directory".
        cors_origins: Extra allowed browser origins. The bundled frontend is served
            same-origin and needs none.
        db: Database settings.
        auth: Authentication settings.
        metrics: Metrics settings.
        config_path: The file these settings were loaded from, if any. Set by the
            loader; not a user-supplied key.
    """

    host: str = "0.0.0.0"  # noqa: S104 - a self-hosted server binds all interfaces
    port: int = 8080
    data_dir: Path = Path("./data")
    server: ServerKind = ServerKind.GRANIAN
    workers: int = 1
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.JSON
    secret_key: str = ""
    cors_origins: tuple[str, ...] = ()
    db: DatabaseSettings = msgspec.field(default_factory=DatabaseSettings)
    auth: AuthSettings = msgspec.field(default_factory=AuthSettings)
    metrics: MetricsSettings = msgspec.field(default_factory=MetricsSettings)
    config_path: Path | None = None

    @property
    def database_url(self) -> str:
        """Return the effective database URL, deriving the SQLite default if unset."""
        if self.db.url:
            return self.db.url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'velox.db').as_posix()}"

    @property
    def is_sqlite(self) -> bool:
        """Whether the effective database is SQLite."""
        return self.database_url.startswith("sqlite")


def _discover_config_path(explicit: str | os.PathLike[str] | None) -> Path | None:
    """Find the configuration file to load, if there is one."""
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise SettingsError(f"configuration file not found: {path}")
        return path
    for candidate in (
        Path(os.environ[f"{ENV_PREFIX}CONFIG"])
        if f"{ENV_PREFIX}CONFIG" in os.environ
        else None,
        Path.cwd() / CONFIG_FILENAME,
        Path("/etc/velox") / CONFIG_FILENAME,
    ):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file into a plain dict, with a readable error on failure."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise SettingsError(f"{path}: cannot be read: {exc}") from exc


def _collect_env_overrides(
    struct: type[msgspec.Struct], prefix: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Build a nested dict of values taken from the environment."""
    overrides: dict[str, Any] = {}
    for field in msgspec.structs.fields(struct):
        path = (*prefix, field.name)
        nested = field.type
        if isinstance(nested, type) and issubclass(nested, msgspec.Struct):
            child = _collect_env_overrides(nested, path)
            if child:
                overrides[field.name] = child
            continue
        variable = ENV_PREFIX + "_".join(path).upper()
        raw = os.environ.get(variable)
        if raw is None:
            continue
        overrides[field.name] = _split_list(raw) if field.name == "cors_origins" else raw
    return overrides


def _split_list(raw: str) -> list[str]:
    """Split a comma-separated environment value into a list."""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge ``overlay`` into ``base``, recursing into nested dicts."""
    merged = dict(base)
    for key, value in overlay.items():
        current = merged.get(key)
        merged[key] = (
            _deep_merge(current, value)
            if isinstance(current, dict) and isinstance(value, dict)
            else value
        )
    return merged


def _decode_custom(target: type, value: Any) -> Any:
    """Decode types msgspec does not handle natively.

    Only ``Path`` is needed today; the hook raises for anything else so that adding
    an unsupported annotation to :class:`Settings` fails loudly rather than silently.
    """
    if target is Path:
        return Path(str(value))
    raise NotImplementedError(f"unsupported setting type: {target!r}")


def _resolve_secret_key(settings: Settings) -> str:
    """Return the secret key, generating and persisting one on first run.

    The key encrypts provider credentials at rest (ADR-0013). It is written with
    ``0600`` permissions into the data directory so that a plain ``docker run`` works
    without the operator having to invent one, while an explicit
    ``VELOX_SECRET_KEY`` still takes precedence.
    """
    if settings.secret_key:
        return settings.secret_key
    key_path = settings.data_dir / "secret.key"
    if key_path.is_file():
        return key_path.read_text(encoding="ascii").strip()
    key = secrets.token_urlsafe(48)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key, encoding="ascii")
    key_path.chmod(0o600)
    return key


def load_settings(config_path: str | os.PathLike[str] | None = None) -> Settings:
    """Load, merge and validate configuration.

    Precedence, lowest to highest: struct defaults, the TOML file, the environment.

    Args:
        config_path: Explicit path to a configuration file. When omitted, the loader
            checks ``VELOX_CONFIG``, ``./velox.toml`` and ``/etc/velox/velox.toml``.

    Returns:
        A validated, frozen :class:`Settings`.

    Raises:
        SettingsError: If the file cannot be read, or a value is invalid. The message
            names the offending setting and what was expected.
    """
    path = _discover_config_path(config_path)
    data: dict[str, Any] = _read_toml(path) if path is not None else {}
    data = _deep_merge(data, _collect_env_overrides(Settings))
    data.pop("config_path", None)

    try:
        # strict=False coerces the strings that necessarily come out of the
        # environment into ints, floats, bools and enums.
        settings = msgspec.convert(data, Settings, strict=False, dec_hook=_decode_custom)
    except msgspec.ValidationError as exc:
        source = f"{path}" if path is not None else "environment"
        raise SettingsError(f"invalid configuration from {source}: {exc}") from exc

    settings = msgspec.structs.replace(
        settings,
        data_dir=settings.data_dir.expanduser(),
        config_path=path,
    )
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SettingsError(
            f"data directory {settings.data_dir} is not writable: {exc}"
        ) from exc

    return msgspec.structs.replace(settings, secret_key=_resolve_secret_key(settings))
