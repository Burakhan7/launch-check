"""Arka plan işleri."""

from typing import Any


async def hello(ctx: dict[str, Any], name: str = "world") -> str:
    """M0 duman testi işi."""
    return f"hello {name}"
