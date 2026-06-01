from __future__ import annotations

import json
import sys
from pathlib import Path

import librosa
import numpy as np

def main() -> int:
    audio_path = Path(sys.argv[1]).resolve()
    y, sr = librosa.load(audio_path, sr=None)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
    result = {
        'sample_rate': int(sr),
        'num_samples': int(len(y)),
        'duration_sec': float(len(y) / sr),
        'feature_type': 'mfcc',
        'shape': [int(mfcc.shape[0]), int(mfcc.shape[1])],
        'mean': float(np.mean(mfcc)),
        'std': float(np.std(mfcc)),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
