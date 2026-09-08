# SURE Core

Host-neutral contracts and deterministic workflow primitives shared by portable
SURE skills and the Pi SURE Harness. The default package entry point does not
import Pi, access the filesystem, spawn processes, or read global environment
state. Node filesystem adapters are isolated behind the explicit
`@earendil-works/sure-core/node` subpath.

The canonical wire schemas live in `sure/core/contracts/` and are copied into
the package at build time.

The executor registry describes adapter kinds, capability ids, and minimum
trust levels without claiming that an adapter is installed. Deployments inject
`ExecutorPort` implementations into `ExecutorRegistry`; an unregistered remote
or trusted executor therefore remains unavailable instead of falling back to a
local process.

The `@earendil-works/sure-core/evaluation` entry point exposes
`createRegisteredOperationRequest`, the canonical request planner shared by
portable and Pi hosts. `createBoundExecutionReceipt` copies the immutable
request bindings into executor receipts. Hosts still allocate ids and time,
admit paths, execute processes, and provide capability evidence; neither
constructor grants executor trust or workflow advancement.

`ExecutionProvenancePublisher` owns the two-phase request/completion publication
protocol and revalidates the persisted latest/immutable history. A deployment
injects its storage port; the shared Node adapter supplies durable filesystem
writes for both `surectl` and Pi. Publication never advances workflow state or
grants assurance. Those decisions remain with the workflow validator and a
host-protected verifier respectively.
