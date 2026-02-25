#!/usr/bin/env python3
"""Shared version loader for paperrss tools."""

from __future__ import annotations

from pathlib import Path

DEFAULT_VERSION = "0.0.0+unknown"


def get_version() -> str:
    path = Path(__file__).resolve().with_name("VERSION")
    try:
        value = path.read_text(encoding="utf-8").strip()
        return value or DEFAULT_VERSION
    except OSError:
        return DEFAULT_VERSION
