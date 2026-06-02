"""pytest bootstrap — put repo root on sys.path so `polybot.*` imports resolve.

Tests live at repo root (tests/, NOT polybot/) so they never deploy to VPS.
Run in the polybot venv (has runtime deps): uv run --project polybot python -m pytest tests/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
