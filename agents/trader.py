"""Kept for Phase-1 import compatibility. The exec seat's composition now
lives in agents/seats.py (build_seat_options), which serves all seats."""

from __future__ import annotations

from agents.seats import build_seat_options as build_trader_options
from agents.seats import load_seat_config

__all__ = ["build_trader_options", "load_seat_config"]
