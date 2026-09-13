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
import re
import secrets
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any

import msgspec

__all__ = [
    "AuthSettings",
    "DatabaseSettings",
    "EndpointSettings",
    "LogFormat",
    "ProviderSettings",
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


class EndpointSettings(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """One backend configured from a preset.

    Attributes:
        preset: Key into ``providers/presets.toml``: ``lmstudio``, ``vllm``,
            ``custom``, ...
        base_url: Address of the backend. Empty means the preset's default.
        api_key: Credential, when the backend was started with one. Held in memory
            only; configuration-sourced credentials are never written to the database.
        id: Provider id, the first half of every ``model_ref``. Defaults to
            ``<preset>-<n>``.
        name: Label shown in the interface. Defaults to the preset's label.
    """

    preset: str
    base_url: str = ""
    api_key: str = ""
    id: str = ""
    name: str = ""


class ProviderSettings(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Backends configured from the file or the environment.

    Backends can also be added from the interface; those are stored in the database.
    Both kinds coexist, and configuration-sourced ones are read-only in the interface
    so a change to ``velox.toml`` is never silently overridden. A configured host needs
    no API key and an unreachable one is a normal, silent state (ADR-0008) — nothing
    here fails startup.

    Environment variables use the singular ``VELOX_PROVIDER_`` prefix —
    ``VELOX_PROVIDER_OLLAMA_HOSTS`` — with ``VELOX_PROVIDERS_`` accepted as well. Any
    preset can be configured the same way: ``VELOX_PROVIDER_LMSTUDIO_HOSTS`` and
    ``VELOX_PROVIDER_VLLM_API_KEY``.

    Attributes:
        ollama_hosts: Base URLs of Ollama instances, each registered as
            ``ollama-0``, ``ollama-1``, ... in listing order.
        llamacpp_hosts: Base URLs of ``llama-server`` instances, registered as
            ``llamacpp-0``, ``llamacpp-1``, ...
        endpoints: Backends configured from any preset.
        autodiscover: Probe the well-known local ports (11434, 8080, 1234, 8000) at
            startup when no hosts are configured at all, and use whichever answers.
            Never scans the LAN; that stays a manual, explicit action.
    """

    ollama_hosts: tuple[str, ...] = ()
    llamacpp_hosts: tuple[str, ...] = ()
    endpoints: tuple[EndpointSettings, ...] = ()
    autodiscover: bool = True

    @property
    def configured(self) -> bool:
        """Whether any backend was named explicitly."""
        return bool(self.ollama_hosts or self.llamacpp_hosts or self.endpoints)


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
    providers: ProviderSettings = msgspec.field(default_factory=ProviderSettings)
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


_LIST_FIELDS = frozenset({"cors_origins", "ollama_hosts", "llamacpp_hosts"})

# Fields that cannot be expressed as one environment variable; they get dedicated
# handling (see _collect_preset_env) instead of a variable that could never parse.
_ENV_SKIPPED = frozenset({"endpoints"})

# `VELOX_PROVIDER_<PRESET>_HOSTS` / `_API_KEY`. The preset name is matched lazily so
# names that themselves contain underscores (MLX_LM, TEXTGEN_WEBUI) still resolve.
_PRESET_ENV = re.compile(r"^VELOX_PROVIDERS?_([A-Z0-9_]+?)_(HOSTS|API_KEY)$")


def _env_names(path: tuple[str, ...]) -> tuple[str, ...]:
    """Return the environment variable names for a setting, preferred first.

    The ``providers`` section is addressed with a singular prefix,
    ``VELOX_PROVIDER_OLLAMA_HOSTS``, because that is the name the project documents and
    the one people write. The name derived mechanically from the section,
    ``VELOX_PROVIDERS_OLLAMA_HOSTS``, is accepted too, so neither spelling is silently
    ignored.
    """
    mechanical = ENV_PREFIX + "_".join(path).upper()
    if path[0] == "providers" and len(path) > 1:
        return (ENV_PREFIX + "PROVIDER_" + "_".join(path[1:]).upper(), mechanical)
    return (mechanical,)


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
        if field.name in _ENV_SKIPPED:
            continue
        raw = next((os.environ[name] for name in _env_names(path) if name in os.environ), None)
        if raw is None:
            continue
        overrides[field.name] = _split_list(raw) if field.name in _LIST_FIELDS else raw
    return overrides


def _collect_preset_env() -> dict[str, dict[str, Any]]:
    """Collect ``VELOX_PROVIDER_<PRESET>_HOSTS`` and ``_API_KEY`` variables.

    Returns:
        ``{preset: {"hosts": [...], "api_key": "..."}}`` for every preset mentioned.
        Ollama and llama.cpp hosts are ordinary fields and are not included.

    Raises:
        SettingsError: If a variable names a preset that does not exist. A typo in a
            variable name must not quietly configure nothing.
    """
    found: dict[str, dict[str, Any]] = {}
    for name, raw in os.environ.items():
        match = _PRESET_ENV.match(name)
        if match is None:
            continue
        preset, what = match.group(1).lower(), match.group(2)
        if preset in {"ollama", "llamacpp"} and what == "HOSTS":
            continue
        from velox_ui.providers.presets import load_presets

        if preset not in load_presets():
            known = ", ".join(sorted(load_presets()))
            raise SettingsError(
                f"{name}: unknown provider preset {preset!r}. Known presets: {known}."
            )
        entry = found.setdefault(preset, {})
        if what == "HOSTS":
            entry["hosts"] = _split_list(raw)
        else:
            entry["api_key"] = raw.strip()
    return found


def _merge_endpoints(
    configured: list[dict[str, Any]], from_env: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Combine file-configured endpoints with those named in the environment.

    The environment wins, as it does for every other setting: hosts given for a preset
    replace that preset's entries from the file, and an API key alone applies to the
    file's entries for it, or configures the preset at its default address when the
    file has none — which is how ``VELOX_PROVIDER_GROQ_API_KEY`` on its own is enough.
    """
    merged = [
        dict(entry)
        for entry in configured
        if not (
            isinstance(entry, dict) and "hosts" in from_env.get(str(entry.get("preset")), {})
        )
    ]
    for preset, entry in from_env.items():
        api_key = entry.get("api_key", "")
        if "hosts" in entry:
            merged.extend(
                {"preset": preset, "base_url": host, "api_key": api_key}
                for host in entry["hosts"]
            )
            continue
        existing = [item for item in merged if item.get("preset") == preset]
        if existing:
            for item in existing:
                item.setdefault("api_key", api_key)
                if not item["api_key"]:
                    item["api_key"] = api_key
        else:
            merged.append({"preset": preset, "api_key": api_key})
    return merged


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


_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def _validate_endpoints(providers: ProviderSettings, source: str) -> None:
    """Check preset-configured endpoints against the preset catalogue.

    Raises:
        SettingsError: For an unknown preset, a preset with no default address and no
            ``base_url``, an id that cannot appear in a model reference, or two
            endpoints sharing an id.
    """
    from velox_ui.providers.presets import load_presets

    presets = load_presets()
    seen: set[str] = set()
    for index, endpoint in enumerate(providers.endpoints):
        where = f"invalid configuration from {source}: providers.endpoints[{index}]"
        preset = presets.get(endpoint.preset)
        if preset is None:
            known = ", ".join(sorted(presets))
            raise SettingsError(
                f"{where}: unknown preset {endpoint.preset!r}. Known presets: {known}."
            )
        if not (endpoint.base_url or preset.base_url):
            raise SettingsError(f"{where}: preset {endpoint.preset!r} needs a base_url.")
        if endpoint.id and not _PROVIDER_ID.match(endpoint.id):
            raise SettingsError(
                f"{where}: id {endpoint.id!r} must be lowercase letters, digits and "
                "hyphens, at most 40 characters."
            )
        if endpoint.id:
            if endpoint.id in seen:
                raise SettingsError(f"{where}: id {endpoint.id!r} is used twice.")
            seen.add(endpoint.id)


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

    source = f"{path}" if path is not None else "environment"
    preset_env = _collect_preset_env()
    if preset_env:
        providers = data.setdefault("providers", {})
        if not isinstance(providers, dict):
            raise SettingsError(
                f"invalid configuration from {source}: [providers] must be a table"
            )
        providers["endpoints"] = _merge_endpoints(
            list(providers.get("endpoints", [])), preset_env
        )

    try:
        # strict=False coerces the strings that necessarily come out of the
        # environment into ints, floats, bools and enums.
        settings = msgspec.convert(data, Settings, strict=False, dec_hook=_decode_custom)
    except msgspec.ValidationError as exc:
        raise SettingsError(f"invalid configuration from {source}: {exc}") from exc
    if settings.providers.endpoints:
        _validate_endpoints(settings.providers, source)

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
