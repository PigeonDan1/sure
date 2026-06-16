import sys, traceback
sys.path.insert(0, "/workspace/sure-eval/src")
sys.path.insert(0, "/workspace/sure-eval/src/sure_eval/models/asr_fireredasr")
sys.path.insert(0, "/workspace/sure-eval/src/sure_eval/models/asr_fireredasr/fireredasr")

try:
    from fireredasr.models.fireredasr import FireRedAsr
    print("SUCCESS: FireRedAsr imported")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
    traceback.print_exc()
