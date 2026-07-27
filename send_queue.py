import asyncio

import discord

import config

_lock = asyncio.Lock()
_worker_task = None
_worker_lock = asyncio.Lock()
_queue = asyncio.Queue()


async def _send_with_retry(send_coro_factory):
    for attempt in range(5):
        try:
            return await send_coro_factory()
        except discord.HTTPException as exc:
            if exc.status == 429 and attempt < 4:
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is None:
                    retry_after = 1.0
                wait = float(retry_after) + 0.35
                print(f"[send_queue] rate limited — waiting {wait:.1f}s (attempt {attempt + 1})")
                await asyncio.sleep(wait)
                continue
            raise


async def _worker():
    while True:
        send_coro_factory, future = await _queue.get()
        try:
            result = await _send_with_retry(send_coro_factory)
            if not future.done():
                future.set_result(result)
        except Exception as exc:
            print(f"[send_queue] send failed: {exc}")
            if not future.done():
                future.set_exception(exc)
        finally:
            await asyncio.sleep(config.SEND_MIN_INTERVAL)
            _queue.task_done()


async def ensure_worker():
    global _worker_task
    async with _worker_lock:
        if _worker_task is None or _worker_task.done():
            _worker_task = asyncio.create_task(_worker())


async def _enqueue(send_coro_factory):
    await ensure_worker()
    future = asyncio.get_running_loop().create_future()
    await _queue.put((send_coro_factory, future))
    return await future


async def queued_send(channel, content, **kwargs):
    async def _factory():
        return await channel.send(content, **kwargs)

    return await _enqueue(_factory)


async def queued_reply(message, content, **kwargs):
    async def _factory():
        return await message.reply(content, **kwargs)

    return await _enqueue(_factory)
