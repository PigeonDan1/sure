# Pip Environment Playbook

Use this only when a model has simple `requirements.txt` or setup.py style
dependencies and no stronger backend signal. Prefer `uv` as the installer when
available, but keep this file as the pip-specific context surface.

## When To Read

- `requirements.txt` exists.
- No `pyproject.toml`, `pixi.toml`, or `environment.yml` drives the setup.
- Dependencies are Python packages and do not require a custom OS image.

## Install Pattern

```bash
cd src/sure_eval/models/<model>
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

If uv is available, prefer:

```bash
uv venv --python=python3.10
uv pip install -r requirements.txt
```

## Record

Write the exact installer choice and reason to `artifacts/backend_choice.json`.

Do not read Docker or conda playbooks unless pip fails due to concrete system,
CUDA, or binary dependency evidence.
