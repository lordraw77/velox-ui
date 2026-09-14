"""The provider preset catalogue.

Presets are loaded from ``presets.toml`` next to this module, once, on first use. The
loader is deliberately light — ``tomllib`` and msgspec, both already imported by the
settings module — because configuration validation consults it at startup, and
startup is spent against the one-second budget.

A malformed catalogue is a packaging bug, not a user error, so it raises immediately
with the offending key rather than degrading to a partial list.
"""

from __future__ import annotations

import ipaddress
import tomllib
from functools import cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import msgspec

__all__ = [
    "OPENAI_STANDARD_PARAMS",
    "Preset",
    "PresetQuirks",
    "get_preset",
    "is_private_address",
    "load_presets",
]

_CATALOGUE = Path(__file__).with_name("presets.toml")

OPENAI_STANDARD_PARAMS: frozenset[str] = frozenset(
    {
        "temperature",
        "top_p",
        "presence_penalty",
        "frequency_penalty",
        "seed",
        "stop",
        "max_tokens",
    }
)
"""Sampling parameters every OpenAI-compatible server accepts under these names."""


class PresetQuirks(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """Protocol deviations of one backend.

    Attributes:
        stream_usage: Request ``stream_options.include_usage`` so the final chunk
            carries token counts.
        max_tokens_field: The request field that caps output length.
    """

    stream_usage: bool = True
    max_tokens_field: str = "max_tokens"


class Preset(msgspec.Struct, frozen=True, forbid_unknown_fields=True):
    """One entry of the catalogue. Field meanings are documented in ``presets.toml``."""

    kind: Literal["ollama", "llamacpp", "openai_compat", "anthropic"]
    label: str
    base_url: str = ""
    auth: Literal["none", "optional", "required"] = "optional"
    local: bool | None = None
    discovery_ports: tuple[int, ...] = ()
    probe_path: str = "/models"
    sampling: tuple[str, ...] = ()
    rename: dict[str, str] = msgspec.field(default_factory=dict)
    docs_url: str = ""
    quirks: PresetQuirks = msgspec.field(default_factory=PresetQuirks)
    key: str = ""

    def supported_params(self) -> frozenset[str]:
        """Every sampling parameter an adapter built from this preset will send."""
        return OPENAI_STANDARD_PARAMS | frozenset(self.sampling)

    def is_local_for(self, base_url: str) -> bool:
        """Whether a backend at ``base_url`` counts as local.

        A preset for software that runs on your own hardware says so explicitly. The
        custom preset does not know, so the address decides: loopback and private
        networks are local, which is what makes a hand-configured LAN server free and
        sorted first rather than priced as a cloud API.
        """
        if self.local is not None:
            return self.local
        return is_private_address(base_url)


def is_private_address(base_url: str) -> bool:
    """Whether a URL points at loopback, a private network, or a ``.local`` name."""
    host = urlsplit(base_url).hostname or ""
    if host in {"localhost", "host.docker.internal"} or host.endswith((".local", ".lan")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


@cache
def load_presets() -> dict[str, Preset]:
    """Load and validate the catalogue.

    Returns:
        Presets keyed by name, in file order.

    Raises:
        ValueError: If an entry does not match the schema.
    """
    with _CATALOGUE.open("rb") as handle:
        raw = tomllib.load(handle)
    presets: dict[str, Preset] = {}
    for key, entry in raw.items():
        try:
            preset = msgspec.convert(entry, Preset)
        except msgspec.ValidationError as exc:
            raise ValueError(f"presets.toml: invalid preset {key!r}: {exc}") from exc
        unknown = set(preset.rename) - preset.supported_params()
        if unknown:
            raise ValueError(
                f"presets.toml: preset {key!r} renames parameters it does not list: "
                f"{sorted(unknown)}"
            )
        presets[key] = msgspec.structs.replace(preset, key=key)
    return presets


def get_preset(key: str) -> Preset | None:
    """Return one preset, or ``None`` when the name is unknown."""
    return load_presets().get(key)
