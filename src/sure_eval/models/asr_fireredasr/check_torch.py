import sys, torch, subprocess
print("Python:", sys.executable)
print("Torch version:", torch.__version__)
print("Torch path:", torch.__file__)
print("sys.path:")
for p in sys.path:
    print("  ", p)
print("\npip list | grep -E 'torch|transformers':")
result = subprocess.run([sys.executable, "-m", "pip", "list"], capture_output=True, text=True)
for line in result.stdout.splitlines():
    if "torch" in line.lower() or "transform" in line.lower():
        print("  ", line)
