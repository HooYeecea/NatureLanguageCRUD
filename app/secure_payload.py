"""Encrypted, type-preserving JSON for short-lived mutation payloads."""
import base64
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import UUID
from app.crypto import encrypt_secret, decrypt_secret


def _encode(value):
    if isinstance(value, dict):
        return ["dict", [[k, _encode(v)] for k, v in value.items()]]
    if isinstance(value, list):
        return ["list", [_encode(v) for v in value]]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode()]
    if isinstance(value, (datetime, date, time)):
        return [type(value).__name__, value.isoformat()]
    if isinstance(value, (Decimal, UUID)):
        return [type(value).__name__, str(value)]
    if value is None or isinstance(value, (str, int, float, bool)):
        return ["scalar", value]
    raise TypeError(f"Unsupported snapshot type: {type(value).__name__}")


def _decode(value):
    kind, data = value
    if kind == "dict":
        return {k: _decode(v) for k, v in data}
    if kind == "list":
        return [_decode(v) for v in data]
    decoders = {"scalar": lambda v: v, "bytes": base64.b64decode,
                "datetime": datetime.fromisoformat, "date": date.fromisoformat,
                "time": time.fromisoformat, "Decimal": Decimal, "UUID": UUID}
    return decoders[kind](data)


def dumps(value):
    return "enc:" + encrypt_secret(json.dumps(_encode(value), ensure_ascii=False))


def loads(value):
    if not value.startswith("enc:"):
        return json.loads(value)  # Existing snapshots are migrated on next access.
    return _decode(json.loads(decrypt_secret(value[4:])))


def maintain(conn, table, field, empty):
    # Names are hard-coded by the two callers, never request input.
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(f"UPDATE {table} SET {field} = ?, status = 'expired' WHERE expires_at <= ? AND status != 'expired'", (dumps(empty), now))
    for row in conn.execute(f"SELECT id, {field} FROM {table} WHERE {field} NOT LIKE 'enc:%'").fetchall():
        conn.execute(f"UPDATE {table} SET {field} = ? WHERE id = ?", (dumps(json.loads(row[field])), row["id"]))
