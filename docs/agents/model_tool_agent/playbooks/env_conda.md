# Conda Environment Playbook

Use this when the repo provides `environment.yml` or requires packages that are
more reliable through conda channels.

## When To Read

- `environment.yml` exists.
- The model needs conda-provided system libraries.
- CUDA/PyTorch compatibility is expressed through conda packages.

## Install Pattern

```bash
cd src/sure_eval/models/<model>
conda env create -f environment.yml
conda activate <env_name>
```

Manual fallback:

```bash
conda create -n <env_name> python=3.10 -y
conda activate <env_name>
conda install -c pytorch -c nvidia -c conda-forge pytorch torchaudio
```

## Record

Record env name, channels, Python version, CUDA/PyTorch versions, and any
deviation from upstream `environment.yml` in `artifacts/build_plan.json`.

If conda is used only as a source of evidence but pixi performs the actual
install, read `env_pixi.md` as the execution playbook.
