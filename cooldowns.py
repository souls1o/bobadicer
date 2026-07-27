import time

import config

_last_used: dict[tuple[int, str], float] = {}


def acquire_command_cooldown(channel_id: int, command: str) -> float | None:
    """Reserve a command use if off cooldown. Returns seconds left if blocked."""
    key = (channel_id, command.lower())
    now = time.monotonic()
    last = _last_used.get(key, 0.0)
    remaining = config.COMMAND_COOLDOWN_SECONDS - (now - last)
    if remaining > 0:
        return remaining
    _last_used[key] = now
    return None
