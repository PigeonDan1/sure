# ModelScope Cache Layout Bad Case

## Trigger

Use this memory only when model weights exist on disk but wrapper code cannot
find them, especially when the provider cache path differs from the repo id.

Common evidence:

- `.runtime/modelscope_cache/` exists under the model directory.
- Provider cache escapes characters, for example `.` becoming `___`.
- `weights_manifest.json` points to one path but runtime resolves another.

## Affected Step

`FETCH_WEIGHTS`, wrapper initialization, or local validation startup.

## Minimum Evidence

Collect:

- `find src/sure_eval/models/{model}/.runtime -maxdepth 4 -type f | sort`
- `sed -n '1,160p' src/sure_eval/models/{model}/artifacts/weights_manifest.json`
- the exact wrapper error showing the unresolved checkpoint path.

## Fix Pattern

Keep all downloaded provider cache under the model directory. Do not depend on
global cache state. Update the wrapper to resolve from the manifest or from the
model-local `.runtime/modelscope_cache/` root.

If a provider rewrites path names, record the rewritten path in
`weights_manifest.json` rather than reconstructing it from the repo id at
runtime.

## Verification

Run the model-local validation script from the model directory, then confirm the
wrapper logs the model-local checkpoint path instead of a global cache path.

```bash
cd src/sure_eval/models/{model}
bash docker_validate.sh
```
