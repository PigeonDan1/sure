"""Local validation script for sa_asr_vibevoiceasr."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Add model-local venv to path
venv_lib = Path(__file__).parent / ".venv" / "lib64" / "python3.11" / "site-packages"
venv_lib2 = Path(__file__).parent / ".venv" / "lib" / "python3.11" / "site-packages"
sys.path.insert(0, str(venv_lib))
sys.path.insert(0, str(venv_lib2))

from model import ModelWrapper


def load_fixture(fixture_dir: Path):
    """Load fixture audio paths and ground truth."""
    gt_path = fixture_dir / "gt.jsonl"
    samples = []
    with open(gt_path, "r", encoding="utf-8") as f:
        for line in f:
            samples.append(json.loads(line))
    return samples


def main():
    model_dir = Path(__file__).parent
    fixture_dir = model_dir / "fixture" / "asr" / "default"
    artifacts_dir = model_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    print("=" * 60)
    print("VALIDATE: sa_asr_vibevoiceasr")
    print("=" * 60)

    # Import test
    print("\n[1/4] Import test...")
    try:
        from transformers import AutoProcessor, VibeVoiceAsrForConditionalGeneration
        print("  PASS: imports OK")
    except Exception as e:
        print(f"  FAIL: {e}")
        return 1

    # Load test
    print("\n[2/4] Load test...")
    try:
        wrapper = ModelWrapper()
        wrapper.load()
        print("  PASS: model loaded OK")
    except Exception as e:
        print(f"  FAIL: {e}")
        return 1

    # Infer test
    print("\n[3/4] Inference test...")
    samples = load_fixture(fixture_dir)
    results = []
    for sample in samples:
        audio_path = fixture_dir / sample["audio"]
        try:
            start = time.time()
            result = wrapper.predict(str(audio_path), max_new_tokens=50)
            elapsed = time.time() - start
            results.append({
                "id": sample["id"],
                "audio": sample["audio"],
                "ground_truth": sample["ground_truth"],
                "prediction": result["text"],
                "time_seconds": elapsed,
            })
            print(f"  [{sample['id']}] {elapsed:.1f}s -> {result['text'][:80]}...")
        except Exception as e:
            print(f"  [{sample['id']}] FAIL: {e}")
            results.append({
                "id": sample["id"],
                "audio": sample["audio"],
                "ground_truth": sample["ground_truth"],
                "prediction": f"ERROR: {e}",
                "time_seconds": 0,
            })

    # Contract test
    print("\n[4/4] Contract test...")
    all_nonempty = all(r["prediction"] and len(r["prediction"]) > 0 for r in results)
    if all_nonempty:
        print("  PASS: all predictions non-empty")
    else:
        print("  FAIL: some predictions are empty")
        return 1

    # Save sample_output.json
    sample_output = {
        "model": "sa_asr_vibevoiceasr",
        "task": "ASR",
        "samples": results,
        "summary": {
            "total": len(results),
            "success": sum(1 for r in results if not r["prediction"].startswith("ERROR")),
            "avg_time_seconds": sum(r["time_seconds"] for r in results) / max(len(results), 1),
        },
    }
    with open(artifacts_dir / "sample_output.json", "w", encoding="utf-8") as f:
        json.dump(sample_output, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("VALIDATION COMPLETE")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
