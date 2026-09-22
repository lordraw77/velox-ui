"""Record the README demo: eight seconds of a real reply streaming in.

Everything here is the real product — the real server, the real frontend bundle, the
real Ollama adapter — with only the model replaced, by ``scripts/demo_backend.py``,
so the recording is reproducible and the speed badge at the end of the reply reports
numbers that came from the stream the viewer just watched.

    python scripts/record_demo.py

It writes ``docs/assets/demo.gif`` and ``docs/assets/screenshot.png``, leaving its
temporary database and video in a scratch directory it removes on the way out.

Requirements: the frontend must be built into ``src/velox_ui/web`` (``npm run build``
in ``frontend/``) and Playwright's Chromium must be installed
(``playwright install chromium``). ffmpeg comes from Playwright's own bundle, so no
system ffmpeg is needed.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, ViewportSize, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"

VIEWPORT: ViewportSize = {"width": 1280, "height": 720}
GIF_WIDTH = 960
GIF_FPS = 12
GIF_SECONDS = 8.0
GIF_COLORS = 128

PROMPT = "How does streaming work in velox-ui?"
TYPING_DELAY_MS = 32

_LEAD_IN_S = 0.6
"""Seconds of the settled interface to keep before the typing starts."""

_TAIL_HOLD_S = 4.0
"""Seconds to keep recording after the reply lands.

Deliberately longer than the GIF has room for. The clip is anchored at its start, so
the surplus is simply trimmed away — but it guarantees there is always enough footage
to fill ``GIF_SECONDS``, whatever the run-to-run variation in page load and title
generation, which is what keeps the output exactly eight seconds every time."""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(url: str, timeout_s: float = 30.0) -> None:
    """Block until a URL answers, or give up with a clear failure."""
    import httpx

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            httpx.get(url, timeout=1.0)
        except httpx.HTTPError:
            time.sleep(0.2)
        else:
            return
    raise SystemExit(f"nothing answered at {url} within {timeout_s:.0f}s")


def _ffmpeg() -> str:
    """Locate an ffmpeg: the system one, or the copy Playwright installs for itself."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    registry = Path(
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache" / "ms-playwright"
    )
    for candidate in sorted(registry.glob("ffmpeg-*/ffmpeg-*"), reverse=True):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise SystemExit("no ffmpeg: install one, or run `playwright install ffmpeg`")


def _to_gif(source: Path, target: Path, *, start_s: float) -> None:
    """Convert the recorded video to a GIF.

    Playwright's bundled ffmpeg is a deliberately minimal build — no GIF muxer and no
    palette filters — so it is used only for what it does have, decoding the WebM to
    scaled PNG frames, and the GIF itself is assembled here. One palette is derived
    from a strip of sampled frames and every frame is quantised to it: a per-frame
    palette would make the interface's greys shift as text arrives.
    """
    from PIL import Image

    frames_dir = source.parent / "frames"
    frames_dir.mkdir(exist_ok=True)
    subprocess.run(  # noqa: S603
        [
            _ffmpeg(),
            "-y",
            "-loglevel",
            "error",
            "-ss",
            f"{max(0.0, start_s):.2f}",
            "-t",
            f"{GIF_SECONDS}",
            "-i",
            str(source),
            "-vf",
            f"scale={GIF_WIDTH}:-2",
            "-r",
            str(GIF_FPS),
            "-an",
            str(frames_dir / "%04d.png"),
        ],
        check=True,
    )

    paths = sorted(frames_dir.glob("*.png"))
    if not paths:
        raise SystemExit("ffmpeg produced no frames from the recording")
    frames = [Image.open(path).convert("RGB") for path in paths]

    # Chromium stops emitting frames once the page stops painting, so the recording
    # ends at the last repaint rather than when the browser closed. Holding the final
    # still — the completed reply and its speed badge — is what makes the clip exactly
    # GIF_SECONDS long on every run, and that last frame is the one worth dwelling on.
    wanted = round(GIF_SECONDS * GIF_FPS)
    frames.extend([frames[-1]] * max(0, wanted - len(frames)))
    del frames[wanted:]

    sample = frames[:: max(1, len(frames) // 12)]
    strip = Image.new("RGB", (frames[0].width, frames[0].height * len(sample)))
    for index, frame in enumerate(sample):
        strip.paste(frame, (0, frame.height * index))
    palette = strip.convert("P", palette=Image.Palette.ADAPTIVE, colors=GIF_COLORS)

    quantised = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    quantised[0].save(
        target,
        save_all=True,
        append_images=quantised[1:],
        duration=round(1000 / GIF_FPS),
        loop=0,
        optimize=True,
        disposal=1,
    )


def _perform(page: Page, base_url: str) -> float:
    """Drive the interface through one complete reply.

    Returns:
        The monotonic time at which the part worth showing began.
    """
    page.goto(base_url, wait_until="networkidle")
    page.wait_for_selector('[data-testid="composer"]', timeout=30_000)
    page.select_option('[data-testid="model-select"]', index=0)
    page.wait_for_timeout(int(_LEAD_IN_S * 1000))

    composer = page.locator('[data-testid="composer"]')
    composer.click()
    action_start = time.monotonic()
    composer.type(PROMPT, delay=TYPING_DELAY_MS)
    page.locator('[data-testid="send"]').click()

    # The speed badge only renders once the backend's final counters have arrived, so
    # waiting for it is exactly waiting for the reply to be complete.
    page.wait_for_selector("text=/tok\\/s/", timeout=60_000)
    page.wait_for_timeout(int(_TAIL_HOLD_S * 1000))
    return action_start


def main() -> None:
    """Record the demo and write the assets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-video", action="store_true", help="keep the raw .webm next to the GIF"
    )
    args = parser.parse_args()

    if not (ROOT / "src" / "velox_ui" / "web" / "index.html").exists():
        raise SystemExit("the frontend is not built: run `npm run build` in frontend/")

    ASSETS.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="velox-demo-"))
    backend_port, velox_port = _free_port(), _free_port()
    backend = velox = None

    try:
        backend = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                str(ROOT / "scripts" / "demo_backend.py"),
                "--port",
                str(backend_port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_for_http(f"http://127.0.0.1:{backend_port}/api/tags")

        velox = subprocess.Popen(
            [sys.executable, "-m", "velox_ui", "serve"],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={
                **os.environ,
                # No login screen and no first-run account wizard in the recording.
                "VELOX_AUTH_ENABLED": "false",
                "VELOX_DATA_DIR": str(scratch / "data"),
                "VELOX_PORT": str(velox_port),
                "VELOX_PROVIDER_OLLAMA_HOSTS": f"http://127.0.0.1:{backend_port}",
                "VELOX_PROVIDERS_AUTODISCOVER": "false",
                "VELOX_METRICS_ENABLED": "false",
            },
        )
        base_url = f"http://127.0.0.1:{velox_port}"
        _wait_for_http(f"{base_url}/health")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport=VIEWPORT,
                device_scale_factor=1,
                color_scheme="dark",
                # The README is in English; the interface follows the browser, so the
                # recording has to pin the locale rather than inherit the host's.
                locale="en-US",
                record_video_dir=str(scratch / "video"),
                record_video_size=VIEWPORT,
            )
            # The video starts with the context, so the interesting part has to be
            # located within it rather than assumed to start at zero.
            recording_started = time.monotonic()
            page = context.new_page()
            action_start = _perform(page, base_url)
            page.screenshot(path=str(ASSETS / "screenshot.png"))
            context.close()
            browser.close()

        video = next((scratch / "video").glob("*.webm"))
        start = action_start - recording_started - _LEAD_IN_S
        _to_gif(video, ASSETS / "demo.gif", start_s=start)
        if args.keep_video:
            shutil.copy2(video, ASSETS / "demo.webm")
    finally:
        for process in (velox, backend):
            if process is not None:
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=10)
        shutil.rmtree(scratch, ignore_errors=True)

    gif = ASSETS / "demo.gif"
    print(f"{gif.relative_to(ROOT)}  {gif.stat().st_size / 1_048_576:.1f} MB")
    print(f"{(ASSETS / 'screenshot.png').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
