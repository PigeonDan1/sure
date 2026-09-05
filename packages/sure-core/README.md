# SURE Core

Host-neutral contracts and deterministic workflow primitives shared by portable
SURE skills and the Pi SURE Harness. This package does not import Pi, access the
filesystem, spawn processes, or read global environment state.

The canonical wire schemas live in `sure/core/contracts/` and are copied into
the package at build time.
