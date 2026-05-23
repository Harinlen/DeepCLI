#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[1]
print((root / "references" / "skill-sources.json").read_text(encoding="utf-8"))

