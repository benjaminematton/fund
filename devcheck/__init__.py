"""Read-only production health checks for developers.

Every check answers: is a stated invariant or a phase acceptance criterion
still true on the box that trades? Incidents validate this list; they never
source it. See docs/superpowers/specs/2026-08-21-day-bookends-design.md §2.4.

Pure: no I/O, no clock, no LLM imports. scripts/dev_status.py is the only
place that talks to a droplet, a broker, or a database.
"""
