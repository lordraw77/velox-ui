"""Column types and annotated aliases shared by every ORM model.

Three conventions are encoded here (docs/design/02-db-schema.md):

* Primary keys are 26-character ULID strings.
* Timestamps are integer epoch milliseconds, never native datetimes: comparing and
  sorting integers needs no dialect-specific adapter and carries no timezone ambiguity.
* Loosely structured data lives in a ``meta``-style column, stored as msgpack in a BLOB
  on SQLite and as ``JSONB`` on PostgreSQL. It is never filtered on in a hot query.
"""

from __future__ import annotations

from typing import Annotated, Any

import msgspec
from sqlalchemy import BigInteger, Dialect, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import mapped_column
from sqlalchemy.types import TypeDecorator

from velox_ui.ids import ULID_LENGTH

__all__ = [
    "BoolInt",
    "Json",
    "PackedJson",
    "Timestamp",
    "UlidPk",
    "UlidRef",
    "shortstr",
]


class PackedJson(TypeDecorator[Any]):
    """Structured column stored as msgpack on SQLite and ``JSONB`` on PostgreSQL.

    msgpack is roughly half the size of JSON text for the payloads stored here and
    decodes faster; PostgreSQL gets native ``JSONB`` instead, because being able to
    inspect the column with ordinary SQL is worth more there than the size saving.
    """

    impl = LargeBinary
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        """Pick the concrete column type for the dialect in use."""
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(LargeBinary())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        """Encode a Python object for storage."""
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return msgspec.msgpack.encode(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        """Decode a stored value back into Python."""
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value
        return msgspec.msgpack.decode(value)


# ULID primary key: fixed width, so SQLite stores it compactly and PostgreSQL can use
# CHAR semantics without padding surprises.
UlidPk = Annotated[str, mapped_column(String(ULID_LENGTH), primary_key=True)]

# A ULID pointing at another row. Foreign keys are declared at the model, not here.
UlidRef = Annotated[str, mapped_column(String(ULID_LENGTH))]

# Epoch milliseconds, UTC. BigInteger because a 32-bit column overflows in 1970 + 24 days.
Timestamp = Annotated[int, mapped_column(BigInteger)]

# SQLite has no boolean type; storing an integer keeps both dialects on one code path
# and keeps partial-index predicates simple.
BoolInt = Annotated[bool, mapped_column()]

# Free-form structured payload.
Json = Annotated[Any, mapped_column(PackedJson)]


def shortstr(length: int = 255) -> Any:
    """Return a mapped column for a bounded string.

    Args:
        length: Maximum length. PostgreSQL enforces it; SQLite ignores it, which is
            why user input is still validated before it reaches the repository.

    Returns:
        A configured ``mapped_column``.
    """
    return mapped_column(String(length))


def longtext() -> Any:
    """Return a mapped column for unbounded text, such as message bodies."""
    return mapped_column(Text)
