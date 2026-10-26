"""Fault controller package.

Modules:

- `catalog`     definitions, validation, UI schema
- `safety`      the compose-label + allowlist boundary
- `flagd`       atomic flag writes with OFREP read-back
- `mechanisms`  container/network/limit/load adapters + symptom probes
- `controller`  lifecycle, audit, events, reconciliation
- `runtime`     watchdog and observer loops
"""
