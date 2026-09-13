"""The preset catalogue and provider configuration from settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from velox_ui.providers.discovery import candidates
from velox_ui.providers.presets import (
    OPENAI_STANDARD_PARAMS,
    get_preset,
    is_private_address,
    load_presets,
)
from velox_ui.services.providers import config_specs
from velox_ui.settings import SettingsError, load_settings

REQUIRED_LOCAL_BACKENDS = {
    "ollama",
    "llamacpp",
    "lmstudio",
    "vllm",
    "tgi",
    "tabbyapi",
    "koboldcpp",
    "localai",
    "jan",
    "llamafile",
    "mlx_lm",
    "textgen_webui",
    "custom",
}


def test_every_required_local_backend_has_a_preset() -> None:
    assert set(load_presets()) >= REQUIRED_LOCAL_BACKENDS


def test_openai_compatible_presets_include_the_api_prefix() -> None:
    for preset in load_presets().values():
        if preset.kind == "openai_compat" and preset.base_url:
            assert preset.base_url.endswith("/v1"), preset.key


def test_local_backends_need_no_key() -> None:
    # ADR-0008: nothing that runs on your own hardware may demand a credential, except
    # a backend that genuinely refuses to start without one.
    for preset in load_presets().values():
        if preset.local and preset.key != "tabbyapi":
            assert preset.auth != "required", preset.key


def test_discovery_probes_only_the_conventional_ports() -> None:
    ports = {
        preset_port
        for preset in load_presets().values()
        for preset_port in preset.discovery_ports
    }
    assert ports == {11434, 8080, 1234, 8000}
    assert ("http://127.0.0.1:1234/v1", get_preset("lmstudio")) in candidates()
    assert all("127.0.0.1" in url for url, _ in candidates()), "loopback only, never the LAN"


def test_supported_params_extend_the_openai_set() -> None:
    vllm = get_preset("vllm")
    assert vllm is not None
    assert vllm.supported_params() >= OPENAI_STANDARD_PARAMS
    assert "num_ctx" not in vllm.supported_params()


@pytest.mark.parametrize(
    ("url", "local"),
    [
        ("http://localhost:1234/v1", True),
        ("http://127.0.0.1:8000/v1", True),
        ("http://192.168.0.140:11434", True),
        ("http://10.1.2.3/v1", True),
        ("http://gpu-box.local:5000/v1", True),
        ("https://api.groq.com/openai/v1", False),
        ("https://8.8.8.8/v1", False),
    ],
)
def test_custom_endpoints_are_local_by_address(url: str, local: bool) -> None:
    assert is_private_address(url) is local
    custom = get_preset("custom")
    assert custom is not None and custom.is_local_for(url) is local


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VELOX_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_documented_singular_env_name_works(isolated, monkeypatch) -> None:
    # This is the name the README and docker-compose use. Before phase 4 only the
    # plural form was read, so a compose file using this name configured nothing.
    monkeypatch.setenv("VELOX_PROVIDER_OLLAMA_HOSTS", "http://192.168.0.140:11434")
    assert load_settings().providers.ollama_hosts == ("http://192.168.0.140:11434",)


def test_the_plural_env_name_still_works(isolated, monkeypatch) -> None:
    monkeypatch.setenv("VELOX_PROVIDERS_LLAMACPP_HOSTS", "http://a:8080,http://b:8080")
    assert load_settings().providers.llamacpp_hosts == ("http://a:8080", "http://b:8080")


def test_any_preset_is_configurable_from_the_environment(isolated, monkeypatch) -> None:
    monkeypatch.setenv(
        "VELOX_PROVIDER_LMSTUDIO_HOSTS", "http://desk:1234/v1,http://lab:1234/v1"
    )
    monkeypatch.setenv("VELOX_PROVIDER_MLX_LM_HOSTS", "http://mac.local:8080/v1")
    monkeypatch.setenv("VELOX_PROVIDER_VLLM_API_KEY", "sk-vllm-secret-9999")
    providers = load_settings().providers

    specs = {spec.provider_id: spec for spec in config_specs(providers)}
    assert set(specs) == {"lmstudio-0", "lmstudio-1", "mlx-lm-0", "vllm-0"}
    assert specs["lmstudio-1"].base_url == "http://lab:1234/v1"
    # A key alone configures the preset at its default address.
    assert specs["vllm-0"].base_url == "http://localhost:8000/v1"
    assert specs["vllm-0"].credential is not None
    assert specs["vllm-0"].credential() == "sk-vllm-secret-9999"
    assert "secret" not in (specs["vllm-0"].credential_hint or "")
    assert "sk-vllm-secret" not in repr(specs["vllm-0"])


def test_a_mistyped_preset_variable_fails_loudly(isolated, monkeypatch) -> None:
    monkeypatch.setenv("VELOX_PROVIDER_LMSTUDOI_HOSTS", "http://desk:1234/v1")
    with pytest.raises(SettingsError, match="lmstudoi"):
        load_settings()


def test_the_environment_replaces_a_preset_from_the_file(isolated, monkeypatch) -> None:
    (isolated / "velox.toml").write_text(
        '[[providers.endpoints]]\npreset = "vllm"\nbase_url = "http://old:8000/v1"\n'
        '[[providers.endpoints]]\npreset = "jan"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("VELOX_PROVIDER_VLLM_HOSTS", "http://new:8000/v1")
    endpoints = load_settings().providers.endpoints
    assert [(item.preset, item.base_url) for item in endpoints] == [
        ("jan", ""),
        ("vllm", "http://new:8000/v1"),
    ]


def test_a_custom_endpoint_needs_an_address(isolated) -> None:
    (isolated / "velox.toml").write_text(
        '[[providers.endpoints]]\npreset = "custom"\n', encoding="utf-8"
    )
    with pytest.raises(SettingsError, match="base_url"):
        load_settings()
