"""Container image size.

Target: under 250 MB. The case reads the size of a locally built ``velox-ui`` image; it
skips when Docker is unavailable or the image has not been built, because a missing
build tool is not a performance regression.
"""

from __future__ import annotations

import asyncio
import shutil

from bench.harness import BenchCase, BenchContext, Measurement

TARGET_MB = 250.0
IMAGE = "lordraw/velox-ui:latest"


async def run(context: BenchContext) -> Measurement:
    """Read the size of the built image, if there is one."""
    del context
    docker = shutil.which("docker") or shutil.which("podman")
    if docker is None:
        return Measurement(
            value=None, unit="MB", skipped=True, detail="docker/podman not found"
        )

    process = await asyncio.create_subprocess_exec(
        docker,
        "image",
        "inspect",
        IMAGE,
        "--format",
        "{{.Size}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0 or not stdout.strip():
        return Measurement(
            value=None, unit="MB", skipped=True, detail=f"image {IMAGE} has not been built"
        )

    return Measurement(
        value=int(stdout.strip()) / (1024 * 1024),
        unit="MB",
        target=TARGET_MB,
        detail=f"uncompressed size of {IMAGE}",
    )


CASE = BenchCase(
    name="image_size",
    description="Size of the container image",
    run=run,
    phase=1,
)
