import asyncio

import discord

import config

_lock = asyncio.Lock()
_last_send_at = 0.0


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


async def _paced_send(send_coro_factory):
    """Serialize all outbound sends globally with a minimum gap between them."""
    global _last_send_at
    async with _lock:
        loop = asyncio.get_running_loop()
        now = loop.time()
        delay = config.SEND_MIN_INTERVAL - (now - _last_send_at)
        if delay > 0:
            await asyncio.sleep(delay)
        result = await _send_with_retry(send_coro_factory)
        _last_send_at = loop.time()
        return result


async def ensure_worker():
    """Kept for compatibility — pacing uses a lock, no background worker."""
    return


async def queued_send(channel, content, **kwargs):
    async def _factory():
        return await channel.send(content, **kwargs)

    return await _paced_send(_factory)


async def queued_reply(message, content, **kwargs):
    async def _factory():
        return await message.reply(content, **kwargs)

    return await _paced_send(_factory)
