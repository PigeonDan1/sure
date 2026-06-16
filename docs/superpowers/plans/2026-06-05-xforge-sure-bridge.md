# XForge SURE Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an adapter layer that lets XForge discovery/collection outputs feed SURE dataset preparation and model onboarding without editing existing XForge skills or SURE core flow files.

**Architecture:** Add a top-level `xforge_sure_bridge` package and `scripts/xforge_*` deterministic entrypoints. XForge writes or provides manifests; the bridge validates and converts those manifests into SURE JSONL, model-local checkpoint manifests, and onboarding metadata that SURE can consume.

**Tech Stack:** Python 3.10, pytest, JSON/JSONL manifests, existing SURE directory conventions.

---

### Task 1: Bridge Tests

**Files:**
- Create: `tests/test_xforge_sure_bridge.py`

- [ ] **Step 1: Write failing tests**

Create tests that import `xforge_sure_bridge` and verify:

```python
def test_process_dataset_manifest_writes_sure_jsonl(tmp_path):
    raw_audio = tmp_path / "raw" / "audio" / "sample.wav"
    raw_audio.parent.mkdir(parents=True)
    raw_audio.write_bytes(b"RIFF")
    raw_jsonl = tmp_path / "raw" / "samples.jsonl"
    raw_jsonl.write_text(
        '{"id":"utt1","audio":"audio/sample.wav","text":"hello","language":"en"}\n',
        encoding="utf-8",
    )
    manifest = {
        "resource_type": "dataset",
        "dataset_id": "demo/asr",
        "sure_name": "demo_asr",
        "task": "ASR",
        "language": "en",
        "raw_root": str(tmp_path / "raw"),
        "raw_jsonl": str(raw_jsonl),
        "field_mapping": {"key": "id", "path": "audio", "target": "text"},
    }
    output = tmp_path / "sure" / "demo_asr.jsonl"

    summary = process_dataset_manifest(manifest, output)

    assert summary["samples_written"] == 1
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "key": "utt1",
        "path": str(raw_audio.resolve()),
        "target": "hello",
        "task": "ASR",
        "language": "en",
        "dataset": "demo_asr",
    }
```

```python
def test_model_manifest_materializes_model_local_weights(tmp_path):
    downloaded = tmp_path / "downloaded" / "model.bin"
    downloaded.parent.mkdir(parents=True)
    downloaded.write_bytes(b"weights")
    model_dir = tmp_path / "src" / "sure_eval" / "models" / "demo_model"
    manifest = {
        "resource_type": "model",
        "model_name": "demo_model",
        "task_type": "asr",
        "source": {"provider": "local", "id": str(downloaded)},
    }

    summary = materialize_model_manifest(manifest, model_dir)

    weights_manifest = json.loads((model_dir / "artifacts" / "weights_manifest.json").read_text())
    assert summary["local_model_path"] == str((model_dir / "checkpoints" / "model.bin").resolve())
    assert weights_manifest["cache_policy"] == "model_local_first"
    assert Path(weights_manifest["resolved_local_model_path"]).exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_xforge_sure_bridge.py -q
```

Expected: import failure because `xforge_sure_bridge` does not exist.

### Task 2: Bridge Library

**Files:**
- Create: `xforge_sure_bridge/__init__.py`
- Create: `xforge_sure_bridge/bridge.py`

- [ ] **Step 1: Implement dataset conversion**

Add `process_dataset_manifest(manifest, output_path)` that:
- validates required fields
- reads raw JSONL
- maps `key`, `path`, and `target`
- resolves relative audio paths under `raw_root`
- writes SURE JSONL
- returns a summary dict.

- [ ] **Step 2: Implement model-local materialization**

Add `materialize_model_manifest(manifest, model_dir)` that:
- supports local source files/directories
- copies them under `checkpoints/`
- creates `.runtime/` and `artifacts/`
- writes `artifacts/weights_manifest.json`
- returns a summary dict.

- [ ] **Step 3: Run focused tests**

Run:

```bash
pytest tests/test_xforge_sure_bridge.py -q
```

Expected: all tests pass.

### Task 3: Deterministic Script Entrypoints

**Files:**
- Create: `scripts/xforge_process_to_sure.py`
- Create: `scripts/xforge_collect_model.py`

- [ ] **Step 1: Add data processing CLI**

`scripts/xforge_process_to_sure.py` accepts:

```bash
python scripts/xforge_process_to_sure.py \
  --manifest path/to/dataset_manifest.json \
  --output data/datasets/xforge_sure/demo_asr.jsonl \
  --summary data/datasets/xforge_sure/demo_asr.summary.json
```

- [ ] **Step 2: Add model collection CLI**

`scripts/xforge_collect_model.py` accepts:

```bash
python scripts/xforge_collect_model.py \
  --manifest path/to/model_manifest.json \
  --model-dir src/sure_eval/models/demo_model \
  --summary src/sure_eval/models/demo_model/artifacts/xforge_collect_summary.json
```

- [ ] **Step 3: Add CLI smoke coverage to tests**

Extend `tests/test_xforge_sure_bridge.py` with subprocess-free calls to the library functions. Do not test network downloads.

### Task 4: Bridge Documentation

**Files:**
- Create: `docs/agents/model_tool_agent/playbooks/xforge_sure_bridge.md`

- [ ] **Step 1: Document the manifest contract**

Include dataset and model manifest examples, output paths, and how these scripts connect to:
- `scripts/prepare_sure_dataset.py`
- `docs/agents/model_tool_agent/AGENTS.md` `FETCH_WEIGHTS`

- [ ] **Step 2: Document non-goals**

State explicitly that the bridge does not edit XForge skills or SURE core agent contracts.

### Task 5: Verification

- [ ] **Step 1: Run focused tests**

```bash
pytest tests/test_xforge_sure_bridge.py -q
```

- [ ] **Step 2: Run script help checks**

```bash
python scripts/xforge_process_to_sure.py --help
python scripts/xforge_collect_model.py --help
```

- [ ] **Step 3: Inspect changed files**

```bash
git -c safe.directory=/mnt/cloudstorfs/sjtu_home/junhao.du/sure-eval-sandbox status --short
```
