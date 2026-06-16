# Pixi Environment Playbook

Use this when `pixi.toml` exists or when a conda-style environment should be
made more reproducible with a lockfile.

## When To Read

- `pixi.toml` exists.
- A conda environment needs lockfile-based reproduction.
- The model has mixed Python and system-library dependencies that fit conda
  channels.

## Install Pattern

```bash
cd src/sure_eval/models/<model>
pixi install
pixi run python validate.py
```

If starting from scratch:

```bash
pixi init
pixi add python=3.10
pixi add pytorch torchaudio
```

## Record

Keep `pixi.lock` model-local when the model owns the environment. Record
channels, selected Python version, and CUDA/PyTorch package choices in
`artifacts/build_plan.json`.

If upstream only ships `environment.yml`, read `env_conda.md` for evidence and
then document why pixi was chosen as the executor.
