# Fixture Contract Mismatch Bad Case

## Trigger

Use this memory when prediction output is non-empty but validation fails because
required fields, task labels, JSON types, or file paths do not match the
declared contract.

Common evidence:

- JSONL parses successfully but evaluator reports missing keys.
- `task_type` or `model_type` disagrees with the selected task playbook.
- Predictions contain a nested object where evaluator expects a string or
number.

## Affected Step

Smoke validation, contract validation, and evaluator handoff.

## Minimum Evidence

Collect:

- selected task playbook and fixture README
- `model.spec.yaml` input/output contract section
- first three lines of predictions and ground truth JSONL
- exact validator error.

## Fix Pattern

Do not change fixture semantics to fit a wrapper bug. Update wrapper output to
match the task contract. If the task genuinely needs a new schema, update the
contract and evaluator together in a separate reviewed change.

## Verification

Run a small JSONL schema check and then the model-local validation command.

```bash
python validate.py --fixture fixture
```
