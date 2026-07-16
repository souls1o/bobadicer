import asyncio

import discord

# Soft global pacing. Discord's real limits are dynamic — 429 retry below
# is what actually backs off when Discord says to wait.
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


def _mark_sent():
    global _last_send_at
    _last_send_at = asyncio.get_running_loop().time()


async def _send_with_retry(send_coro_factory):
    for attempt in range(5):
        try:
            result = await send_coro_factory()
            _mark_sent()
            return result
        except discord.HTTPException as exc:
            if exc.status == 429 and attempt < 4:
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is None:
                    retry_after = 1.0
                wait = float(retry_after) + 0.35
                print(f"[send_queue] rate limited — waiting {wait:.1f}s (attempt {attempt + 1})")
                await asyncio.sleep(wait)
                _mark_sent()  # treat cooldown as "just sent" so next gap is full interval
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
