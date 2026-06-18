"""
ambient_memory — Automated memory capture system for Hermes Agent.

Core modules:
    capture     — Extraction, dedup, and write logic (used by hooks + cron)
    sweep       — Cron poller that scans ended sessions and captures facts
    consolidate — Weekly trust maintenance (decay, promote, supersession)
"""

__version__ = "1.1.0"
