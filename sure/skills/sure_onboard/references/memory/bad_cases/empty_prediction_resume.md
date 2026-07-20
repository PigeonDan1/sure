# Empty Prediction Resume Bad Case

## Trigger

Use this memory when prediction generation skips work because resume mode sees
an existing empty or partial output file.

Common evidence:

- output JSONL exists but has zero bytes or too few lines.
- logs say work was skipped or resumed.
- rerunning without deleting the output reproduces the empty result.

## Affected Step

Main-flow prediction generation and model-local validation scripts that reuse
existing output paths.

## Minimum Evidence

Collect:

- output file path, size, and line count
- command flags controlling resume/overwrite behavior
- logs around skip/resume decisions.

## Fix Pattern

Treat zero-byte or incomplete prediction files as invalid resume targets. Use a
temporary output file during generation, then move it into place only after
successful completion.

For manual validation, remove only the generated prediction file for the current
test run. Do not delete model source files, fixtures, or checkpoints.

## Verification

Run the same command twice:

1. first run creates non-empty predictions;
2. second run either resumes a complete file or regenerates an invalid file.

Confirm line count matches the selected fixture count.
