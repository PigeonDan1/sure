# SURE Core

Host-neutral contracts and deterministic workflow primitives shared by portable
SURE skills and the Pi SURE Harness. This package does not import Pi, access the
filesystem, spawn processes, or read global environment state.

The canonical wire schemas live in `sure/core/contracts/` and are copied into
the package at build time.

The executor registry describes adapter kinds, capability ids, and minimum
trust levels without claiming that an adapter is installed. Deployments inject
`ExecutorPort` implementations into `ExecutorRegistry`; an unregistered remote
or trusted executor therefore remains unavailable instead of falling back to a
local process.
