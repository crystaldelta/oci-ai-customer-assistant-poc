"""The `memory` seam: the LangGraph checkpointer that holds conversations, chosen
by `MEMORY` and keyed by thread key.

- `sqlite` (local default): survives API restarts, but only on one machine and
  with no expiry, so it is for local use only.
- `in_process`: `MemorySaver`, lost on restart. The tests use it.
- `agentcore`: AgentCore Memory, short-term only (≤ 7 days); accepted, built in
  Phase 6 with the `ai-project-aws` skill (`langgraph-checkpoint-aws`).
"""

import asyncio
import sqlite3

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from assistant.config import Settings


class ThreadedSqliteSaver(SqliteSaver):
    """`SqliteSaver` that also serves the async API, by running the sync methods
    on a worker thread.

    The service answers both synchronously (`/api/ask`) and by streaming
    (`/api/ask/stream`, which needs the async methods). `SqliteSaver` refuses
    async calls, and `AsyncSqliteSaver` refuses sync ones and is tied to one
    event loop. The sync methods take the saver's own lock around a connection
    opened with `check_same_thread=False`, so calling them from worker threads
    is safe.
    """

    async def aget_tuple(self, config):
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        items = await asyncio.to_thread(lambda: list(self.list(config, filter=filter, before=before, limit=limit)))
        for item in items:
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        return await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id):
        return await asyncio.to_thread(self.delete_thread, thread_id)


def get_checkpointer(settings: Settings) -> BaseCheckpointSaver:
    if settings.memory == "in_process":
        return MemorySaver()
    if settings.memory == "sqlite":
        settings.memory_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.memory_path, check_same_thread=False)
        return ThreadedSqliteSaver(conn)
    raise NotImplementedError(f"MEMORY={settings.memory} arrives in Phase 6")
