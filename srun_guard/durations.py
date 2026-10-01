"""Human-readable durations; configuration and scheduling always use integer seconds."""

import math


def format_duration(seconds: float) -> str:
    value = max(0, math.ceil(seconds))
    hours, rest = divmod(value, 3600)
    minutes, seconds = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} 小时")
    if minutes:
        parts.append(f"{minutes} 分钟")
    if seconds or not parts:
        parts.append(f"{seconds} 秒")
    return " ".join(parts)


def short_duration(seconds: float) -> str:
    """Compact English duration for logs, not localized UI labels."""
    value = max(0, math.ceil(seconds))
    if value and value % 3600 == 0:
        return f"{value // 3600}h"
    if value and value % 60 == 0:
        return f"{value // 60}m"
    return f"{value}s"
