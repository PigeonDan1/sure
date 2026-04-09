# WeSpeaker Onboarding Case in SURE-EVAL

This document summarizes the **actual onboarding process** of WeSpeaker in SURE-EVAL, with focus on three questions:

1. how we currently configure environments in SURE-EVAL,
2. why WeSpeaker failed at first,
3. how it was later recovered successfully.

This is a **process-oriented onboarding case note** for harness engineering and future architecture upgrades.

---

## 1. What SURE-EVAL Is Doing During Model Onboarding

SURE-EVAL does not treat onboarding as manual trial-and-error.  
Instead, it uses a **Harness-First workflow** to turn an external repository into a reproducible local runnable unit.

For phase-1 onboarding, the goal is narrow:

- verify the minimal repo-native path,
- confirm the model can be imported,
- load the minimal callable target,
- run one local test case,
- check whether the output satisfies the expected contract.

The standard phase-1 flow is:

`DISCOVER -> CLASSIFY -> PLAN -> VALIDATE_SPEC -> BUILD_ENV -> FETCH_WEIGHTS -> VALIDATE_IMPORT -> VALIDATE_LOAD -> VALIDATE_INFER -> VALIDATE_CONTRACT -> GENERATE_WRAPPER -> SAVE_ARTIFACTS`

So the question is not just “can this repo run once”, but:

- can it run in an isolated environment,
- can the minimal path be validated,
- can the result be recorded as structured artifacts,
- can the same setup be reproduced later.

---

## 2. How We Currently Configure Environments in SURE-EVAL

Environment configuration in SURE-EVAL has **multiple layers**, not just “which Python version to use”.

### 2.1 Backend choices

Different models may use different environment backends:

- `uv`
- `conda`
- `docker`
- `pip`

This is the first layer of environment setup.  
Different repositories may fit different backends better.

Examples from the current model set:

- many ASR tools use `uv`
- some heavier or more brittle models use `conda`
- WeSpeaker was finally handled with `pip`

So backend choice is **model-specific**, not globally fixed.

---

### 2.2 Model-local isolated environment

SURE prefers **model-local isolation**.

That means we try not to modify:

- system Python
- global pip
- base conda
- shell startup files
- host-level package state

Instead, each model should ideally keep its own:

- local virtual environment
- local cache
- local artifacts
- local wrapper files

This is important because onboarding is not only about making a repo run once.  
It must also avoid contaminating the host machine and remain reproducible.

For WeSpeaker, both the failed run and the successful run were kept in a **model-local environment**, rather than changing the host global setup.

---

### 2.3 Install-route choice

Even under the same backend, installation can still happen in different ways.

Typical options include:

- thin package install
- upstream development install
- editable install from a fixed source copy

This turned out to matter a lot for WeSpeaker.

A backend name alone is not enough.  
The real question is:

- was it installed as a thin package,
- or via upstream `requirements.txt`,
- or as editable local source,
- and was the upstream commit fixed?

These details strongly affect reproducibility and import stability.

---

### 2.4 Version pinning and recorded patches

For unstable upstream repositories, environment setup may also include:

- fixed commit pinning
- package version pinning
- recorded local patches

These are not ad hoc hacks.  
In SURE, they should be treated as explicit onboarding decisions.

This is especially important for repos whose import path changes over time or whose optional modules widen dependency surface unexpectedly.

---

## 3. What Was the Phase-1 Target for WeSpeaker

The target for WeSpeaker was intentionally small.

We did **not** try to validate the whole repository.  
We only wanted to validate the minimal speaker verification path:

```python
import wespeaker

model = wespeaker.load_model("english")
model.set_device("cpu")
score = model.compute_similarity(
    "tests/fixtures/shared/speaker_verification/spk1_enroll.wav",
    "tests/fixtures/shared/speaker_verification/spk1_trial.wav",
)
```

That means this phase was **not** about:

- diarization benchmarking
- training
- calibration
- alternative frontends
- production optimization

The question was only:

**Can the repo-native minimal English speaker verification path be imported, loaded, executed, and wrapped under SURE?**

---

## 4. Why WeSpeaker Failed Initially

The first important point is:

**WeSpeaker did not initially fail because of the fixture.**  
It also did not fail because the similarity-scoring logic itself was already proven wrong.

It failed earlier.

### 4.1 Failure stage

The initial run stopped at:

- `VALIDATE_IMPORT`

So the failure happened **before**:

- model loading,
- weight usage,
- inference,
- output contract validation.

This is already a strong hint that the problem was not in the test audio or scoring logic.

---

### 4.2 The real mechanism of failure

The core issue was the **import chain** of the upstream repository.

What happened was roughly:

1. `import wespeaker`
2. this eagerly imported `wespeaker.cli.speaker`
3. that eagerly imported `wespeaker.frontend`
4. `wespeaker.frontend` eagerly pulled in multiple optional frontend families
5. those optional frontends triggered dependencies that were not actually required for the minimal English speaker verification path

So the minimal path was blocked by **unrelated optional components**.

This is the key engineering point.

The repo did not fail because the target path itself was impossible.  
It failed because the package imported **too much too early**.

---

### 4.3 What errors showed up

During the failed run, the import-stage errors appeared in sequence:

1. `ModuleNotFoundError: No module named 's3prl'`
2. `AttributeError: module 'torchaudio' has no attribute 'set_audio_backend'`
3. `ModuleNotFoundError: No module named 'whisper'`

This sequence is important.

It shows that the problem was not just “one missing package”.  
Instead, the import chain kept exposing more optional dependencies and compatibility issues.

So if we had continued blindly installing packages, the environment might have become larger and messier without guaranteeing a clean minimal path.

---

### 4.4 How we classify this failure

This failure should be understood as:

- **first an integration problem**
- **then a dependency problem**
- **not primarily a fixture problem**

That distinction matters.

If we misclassify it as a fixture issue, we waste time checking WAV files.  
If we misclassify it as a simple missing-package issue, we keep blindly expanding dependencies.

But the real issue was:

**the repo-native import boundary was too broad for phase-1 minimal validation.**

---

## 5. Why the Initial Strategy Was Not Enough

The initial failed run exposed three weaknesses.

### 5.1 Thin installation was too weak

The first attempt effectively relied on a thinner installation path.

But WeSpeaker’s real import-time behavior expected more than that, especially once optional frontend families were eager-imported.

So the environment that looked “sufficient” on paper was not sufficient in practice.

---

### 5.2 No fixed commit meant instability

When upstream commit is left unspecified, onboarding becomes much more fragile.

Why?

Because the import surface of the repo may change over time:

- new optional modules may be added,
- imports may become broader,
- dependency expectations may drift.

This makes reproducible onboarding harder.

---

### 5.3 Optional modules were allowed to block the minimal target

This was the biggest structural problem.

For phase-1, the minimal target was only speaker verification.  
But optional frontend subsystems were imported so early that they could break onboarding before the target path even started.

That means the repo’s internal structure was not well aligned with minimal validation.

---

## 6. How WeSpeaker Was Eventually Recovered

WeSpeaker was later successfully onboarded, but not by random retrying.

The successful recovery came from several deliberate changes.

---

### 6.1 Fix the upstream commit

The successful run pinned a concrete upstream commit.

This was necessary because it stabilized:

- code state,
- dependency expectations,
- patch target,
- reproduction conditions.

Without a fixed commit, even a correct patch or install recipe might stop working later.

---

### 6.2 Switch to the upstream development install route

The successful recovery no longer relied on the earlier thinner install assumption.

Instead, it moved closer to the repository’s real development route:

- use upstream `requirements.txt`
- install from a fixed local source copy
- use editable install where needed

This matters because the repo’s true runtime surface was closer to its development setup than to a thin package-only path.

---

### 6.3 Apply a minimal lazy-import patch

This was the most important fix.

A recorded local patch was used to make `wespeaker.frontend` lazily import optional frontends instead of eagerly importing them all at package import time.

That means optional components such as:

- `s3prl`
- `whisper`
- `w2vbert`

would no longer block the minimal speaker verification path unless they were actually needed.

This is a much better fix than blindly installing every optional dependency.

It narrows the active runtime path to match the actual phase-1 target.

---

### 6.4 Pin torch / torchaudio versions

The successful recovery also pinned:

- `torch==2.1.2`
- `torchaudio==2.1.2`

This was necessary because newer combinations introduced extra compatibility problems, including behavior around `torchaudio.load()` and related dependencies.

So success did not come from just “installing more things”.  
It also required **version control**.

---

### 6.5 Keep everything inside a model-local environment

Importantly, the final successful solution did **not** require changing the host global environment.

That means the recovery path remained consistent with the SURE design principle of:

- model-local setup
- host isolation
- reproducible environment state

This is a real success condition, not just a convenience detail.

---

## 7. What the Final Success Actually Means

The successful onboarding means:

- WeSpeaker’s minimal English speaker verification path can be validated in SURE
- the issue was recoverable
- the repo is not fundamentally unusable
- but it does require more careful handling than a simple “pip install and run” model

It also means that for this model, success depended on **four things together**:

1. fixed commit
2. upstream-style install route
3. lazy-import patch
4. dependency version pinning

So this should be treated as a **controlled recovery case**, not a trivial ready-made integration.

---

## 8. What This Case Tells Us About Our Architecture

This case gives several useful lessons for SURE.

### 8.1 Backend name alone is too shallow

We should not summarize environment configuration with just:

- “this model uses pip”
- “this model uses conda”

That loses too much information.

For hard repos, we also need to record:

- install route
- commit pinning
- patch requirement
- version pins
- whether the environment is fully model-local

---

### 8.2 Phase-1 minimal path must be protected

A repo’s optional subsystems should not be allowed to block a phase-1 minimal target.

This suggests our onboarding logic should keep emphasizing:

- minimal callable path first
- optional capabilities later

Otherwise we end up validating the whole import graph instead of the real target path.

---

### 8.3 Blind dependency expansion is not a real strategy

When a repo keeps exposing new optional imports, simply installing more packages is not stable engineering.

A better strategy is:

1. inspect import path
2. identify whether optional modules are being pulled too early
3. decide whether install route needs upgrading
4. decide whether a minimal recorded patch is justified

That is exactly what the WeSpeaker case taught us.

---

### 8.4 Recorded minimal patching is sometimes necessary

This case also shows that recorded local patching can be valid.

Not all patches are bad.

A patch is acceptable when it is:

- minimal
- justified by phase-1 scope
- recorded
- reproducible
- reversible

The WeSpeaker lazy-import change is an example of this.

---

## 9. Practical Summary

### Environment configuration methods we currently use

SURE currently configures environments through a combination of:

- backend selection (`uv`, `conda`, `docker`, `pip`)
- model-local isolated environments
- model-local cache/artifact layout
- install-route choice
- fixed commit pinning when needed
- explicit dependency version pinning
- recorded local patches when justified

### Why WeSpeaker failed at first

It initially failed because:

- the repo stopped at `VALIDATE_IMPORT`
- upstream import chain was too broad
- optional frontends were imported too early
- unrelated optional dependencies blocked the minimal speaker verification path

### How WeSpeaker later succeeded

It later succeeded because:

- upstream code state was fixed with a pinned commit
- install route was upgraded to match upstream development expectations
- optional frontend imports were narrowed via a recorded lazy-import patch
- critical package versions were pinned
- the whole recovery stayed inside a model-local isolated environment

---

## 10. One-Sentence Case Summary

WeSpeaker is a representative SURE onboarding case where the first failure was caused by an overly broad upstream import chain rather than by fixture quality or core speaker-verification logic, and the later success came from fixed-commit pinning, upstream-style installation, minimal lazy-import patching, version pinning, and strict model-local environment isolation.

