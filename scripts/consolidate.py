#!/usr/bin/env python3
"""
Wrapper script for ambient_memory consolidate.

Drop this file into ~/.hermes/scripts/ so Hermes cron can reference it.
Hermes cron requires scripts to live under ~/.hermes/scripts/.

This thin wrapper imports and runs the ambient_memory consolidate module.
"""

import sys
from pathlib import Path

# Point Python at the ambient_memory package
sys.path.insert(0, str(Path.home() / ".hermes" / "ambient_memory"))

from consolidate import main

main()
