"""Upload storage: content sniffing, size caps and path-safe placement on disk.

Bytes are written under ``<data_dir>/uploads/<sha256[:2]>/<sha256>``, so identical
content from different uploads lands on the same path and the directory never grows
one flat listing of thousands of files. The hash is computed from the actual bytes,
never trusted from the client, and the stored filename is never derived from the
client-supplied name — that name is kept only as metadata, so nothing it contains can
influence a filesystem path (ADR referenced: docs/design/02-db-schema.md "Files and
RAG").
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from velox_ui.errors import ValidationError

__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "IMAGE_CONTENT_TYPES",
    "MAX_IMAGE_BYTES",
    "MAX_UPLOAD_BYTES",
    "UploadedContent",
    "sniff_content_type",
    "store_upload",
    "validate_image_upload",
]

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

# RAG document ingestion supports plain text, markdown and PDF for phase 7; other
# loaders (docx, html, csv) are named in docs/design/01-repo-layout.md for a later
# phase and are deliberately not accepted yet.
ALLOWED_CONTENT_TYPES: dict[str, tuple[bytes, ...]] = {
    "text/plain": (),
    "text/markdown": (),
    "application/pdf": (b"%PDF-",),
}


MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Chat attachments for vision models. Identified by magic bytes alone: the extension
# and the declared type are hints a client controls. SVG is left out on purpose -- it
# is a document that can carry script, not a picture a vision model can read.
IMAGE_CONTENT_TYPES: dict[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}


class UploadedContent:
    """A validated upload, ready to be stored."""

    __slots__ = ("content_type", "data", "sha256")

    def __init__(self, data: bytes, content_type: str) -> None:
        """Hash and store the bytes; validation has already passed."""
        self.data = data
        self.content_type = content_type
        self.sha256 = hashlib.sha256(data).hexdigest()


def sniff_content_type(filename: str, declared: str | None, data: bytes) -> str:
    """Resolve a trustworthy content type from the file extension and its bytes.

    Args:
        filename: The client-supplied name, used only for its extension.
        declared: The client-declared ``Content-Type``, a hint at best.
        data: The file's bytes.

    Raises:
        ValidationError: If the type is not one RAG ingestion accepts, or the bytes
            do not match a required magic prefix.
    """
    suffix = Path(filename).suffix.lower()
    by_extension = {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".pdf": "application/pdf",
    }.get(suffix)
    content_type = by_extension or declared or "application/octet-stream"
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError(
            f"Unsupported file type: {content_type!r}. "
            f"Accepted: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}."
        )
    magic_prefixes = ALLOWED_CONTENT_TYPES[content_type]
    if magic_prefixes and not any(data.startswith(prefix) for prefix in magic_prefixes):
        raise ValidationError("The file's content does not match its extension.")
    return content_type


def validate_upload(
    filename: str, declared_content_type: str | None, data: bytes
) -> UploadedContent:
    """Validate size and type, and hash an upload.

    Raises:
        ValidationError: If the upload is empty, too large, or of an unsupported type.
    """
    if not data:
        raise ValidationError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"The uploaded file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
        )
    content_type = sniff_content_type(filename, declared_content_type, data)
    return UploadedContent(data, content_type)


def validate_image_upload(data: bytes) -> UploadedContent:
    """Validate a chat image by size and magic bytes, and hash it.

    Raises:
        ValidationError: If the upload is empty, too large, or not a PNG, JPEG, GIF or
            WebP image.
    """
    if not data:
        raise ValidationError("The uploaded file is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationError(
            f"The image exceeds the {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit."
        )
    for content_type, prefixes in IMAGE_CONTENT_TYPES.items():
        if not any(data.startswith(prefix) for prefix in prefixes):
            continue
        # A RIFF container is also WAV and AVI; WebP says so at offset 8.
        if content_type == "image/webp" and data[8:12] != b"WEBP":
            continue
        return UploadedContent(data, content_type)
    raise ValidationError("Unsupported image. Accepted: PNG, JPEG, GIF and WebP.")


def store_upload(data_dir: Path, content: UploadedContent) -> str:
    """Write validated bytes to disk under the data directory, deduplicated by hash.

    Returns:
        The storage key, relative to ``data_dir``, to save on the ``file`` row.
    """
    digest = content.sha256
    relative = Path("uploads") / digest[:2] / digest
    path = data_dir / relative
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.data)
    return relative.as_posix()


def read_upload(data_dir: Path, storage_key: str) -> bytes:
    """Read a stored upload's bytes back."""
    return (data_dir / storage_key).read_bytes()
