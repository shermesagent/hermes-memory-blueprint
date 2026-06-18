#!/usr/bin/env python3
"""
Wrapper script for ambient_memory sweep.

Drop this file into ~/.hermes/scripts/ so Hermes cron can reference it.
Hermes cron requires scripts to live under ~/.hermes/scripts/.

This thin wrapper imports and runs the ambient_memory sweep module.
"""

import sys
from pathlib import Path

# Point Python at the ambient_memory package
sys.path.insert(0, str(Path.home() / ".hermes" / "ambient_memory"))

from sweep import main

main()
