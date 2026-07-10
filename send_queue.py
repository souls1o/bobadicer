import asyncio

import discord

# Discord allows ~5 msgs/sec; stay under with 4/sec.
_MIN_INTERVAL = 0.25

_lock = asyncio.Lock()
_last_send_at = 0.0
_worker_task = None
_queue = asyncio.Queue()


async def _wait_for_slot():
    global _last_send_at
    now = asyncio.get_running_loop().time()
    delay = _MIN_INTERVAL - (now - _last_send_at)
    if delay > 0:
        await asyncio.sleep(delay)
    _last_send_at = asyncio.get_running_loop().time()


async def _send_with_retry(send_coro_factory):
    for attempt in range(4):
        try:
            return await send_coro_factory()
        except discord.HTTPException as exc:
            if exc.status == 429 and attempt < 3:
                retry_after = getattr(exc, "retry_after", None) or 1.0
                await asyncio.sleep(float(retry_after))
                continue
            raise


async def _worker():
    while True:
        send_coro_factory, future = await _queue.get()
        try:
            async with _lock:
                await _wait_for_slot()
                result = await _send_with_retry(send_coro_factory)
            if not future.done():
                future.set_result(result)
        except Exception as exc:
            print(f"[send_queue] send failed: {exc}")
            if not future.done():
                future.set_exception(exc)
        finally:
            _queue.task_done()


def ensure_worker():
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker())


async def queued_send(channel, content, **kwargs):
    ensure_worker()
    future = asyncio.get_running_loop().create_future()

    async def _factory():
        return await channel.send(content, **kwargs)

    await _queue.put((_factory, future))
    return await future


async def queued_reply(message, content, **kwargs):
    ensure_worker()
    future = asyncio.get_running_loop().create_future()

    async def _factory():
        return await message.reply(content, **kwargs)

    await _queue.put((_factory, future))
    return await future
