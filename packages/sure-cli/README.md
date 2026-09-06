# surectl

`surectl` is the host-neutral SURE control plane. It owns run creation,
structural/registered-validator decisions, capability evidence, cooperative
execution receipts, conformance assessment, resume binding, and finalization;
an agent may supply artifacts and repair work but cannot write a checkpoint
transition directly. `execute` never advances a workflow: only `validate` can
submit a Core validation signal and persist the next checkpoint.

The portable executor is deliberately cooperative. Its receipts identify
`local`, `python`, or `docker` execution, but use `trust_level: cooperative`;
they cannot claim Pi-enforced or trusted formal assurance. Missing or unknown
capabilities produce `NOT_EXECUTED/CAPABILITY_MISSING`.

`surectl capabilities` includes the immutable executor registry. `remote` and
`trusted` are declared extension points, not built-in implementations: until a
deployment registers those adapters, `execute` writes a `NOT_STARTED` receipt
with `CAPABILITY_MISSING` and never launches the request locally.
