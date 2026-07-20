# External Engines

Local harness deployments can place external repositories here. These
checkouts are runtime dependencies, not source owned by this harness repo.

Current harness layout:

```text
sure/external/sure-evaluation  # local checkout, ignored by git
```

Use the standalone evaluation engine:

```bash
git clone https://github.com/PigeonDan1/sure-evaluation.git sure/external/sure-evaluation
```

Do not commit absolute local symlinks. The harness code should discover the
engine through `sure/external/sure-evaluation` by default. Use
`SURE_EVALUATION_HOME` only as an explicit local override.
