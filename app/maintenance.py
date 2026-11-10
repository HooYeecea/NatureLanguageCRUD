"""Erase expired snapshots even when the workbench is idle."""
import asyncio
import logging
from app.meta_db import meta_conn
from app.mutate.pending import ensure_pending_table
from app.mutate.undo import ensure_undo_table


def clean_payloads():
    with meta_conn() as conn:
        ensure_pending_table(conn)
        ensure_undo_table(conn)


async def cleanup_loop():
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(clean_payloads)
        except Exception:
            logging.getLogger(__name__).error("Snapshot cleanup failed; administrator attention required")
