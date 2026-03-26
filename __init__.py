"""
Blender addon package entry.

This file delegates addon lifecycle to `addon.py` so there is a single
implementation source for register/unregister and operators.
"""

from __future__ import annotations

import importlib

try:
    from . import addon as _addon
except Exception:
    import addon as _addon  # type: ignore


# Support Blender script reloading during development.
_addon = importlib.reload(_addon)

bl_info = _addon.bl_info


def register() -> None:
    _addon.register()


def unregister() -> None:
    _addon.unregister()


if __name__ == "__main__":
    register()
