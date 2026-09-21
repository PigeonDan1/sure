# Dataset Formats: Source Pools and OpenBench

## Why this exists

`/sure_eval` reads datasets in the site's source-pool (`ds_pool`) layout. A
dataset that also has to be published to OpenBench must be converted to the
OpenBench layout first. Both formats name their record file `sample.jsonl` with
**incompatible schemas** — this is the reference for that conversion and for
telling the two apart.

This is background, not the execution contract. The default conversion path
produces `sure_eval_jsonl_v1` (see `contracts/eval_run_layout.md`) and never
needs the OpenBench half of this document. Read the OpenBench half only when a
run has to export a source dataset for publication, or explain why the platform
rejected one.

Nothing here replaces the deterministic resolvers. The authoritative code is
`scripts/sure_eval/datasets/source_resolver.py` and
`scripts/sure_eval/datasets/dataset_manager.py`; if this summary disagrees with
them, the code wins. The projector rewrites the mapping it actually applied to
`mapping.yaml` on every run —
`<projection_root>/sure_benchmark/<source_dataset_name>/projections/<proj_id>/mapping.yaml`
— where `proj_id` is `asr_transcription_v1`, `vad_segments_v1`, or
`<task_slug>_readback_v1`.

## Source pool (`ds_pool`) — the format `/sure_eval` consumes

The layout is fixed and enforced at resolution time. A source root must sit at
`.../ds_pool/<source_dataset_name>` under a configured `allowed_source_roots`
entry:

```text
<allowed_source_roots entry>/.../ds_pool/<source_dataset_name>/
├── sample_files/
│   └── <version_id>/
│       ├── sample.jsonl     # one JSON object per record
│       └── ds.jsonl         # one JSON object: dataset-level metadata
└── raws/
    └── sample/              # media, referenced by attribute.path
```

- All three of `sample_files/<version_id>/sample.jsonl`, `.../ds.jsonl`, and
  `raws/sample/` are required; a missing one fails resolution.
- Multiple versions require an explicit trailing `@<version_id>` on the request
  path.
- `dataset_id = <source_dataset_name>__<version_id>`; the report identity carries
  no task suffix.

There is no `README.md` and no YAML front matter on the source side. `ds.jsonl`
is the only metadata carrier:

```json
{"supported_tasks": ["ASR", "TTS"], "audio": {"speech": {"language": "zh"}}}
```

### `sample.jsonl` record

Same shape as the fixtures in `scripts/test_source_conversion.py` (`size: 8` is
placeholder audio bytes, not a real file) — short speech, then segmented:

```json
{"sample_id": "utt1", "attribute": {"path": "utt1.wav", "size": 8, "sample_rate": 16000,
 "duration": 1000, "raw_data_format": "wav", "channels": 1},
 "annotation": [{"transcription": {"text": ["你好"]}}]}
```

```json
{"sample_id": "utt1", "attribute": {"path": "utt1.wav", "duration": 1500, "sample_rate": 16000,
 "channels": 1},
 "annotation": [{"seg_id": "0", "timestamp": {"begin_time": 0.1, "end_time": 0.4}},
                {"seg_id": "1", "timestamp": {"begin_time": 0.8, "end_time": 1.2}}]}
```

| Field | Meaning |
| --- | --- |
| `sample_id` | Record key |
| `attribute.path` | Media path, **relative to `raws/sample/`** |
| `attribute.sample_rate` | Sample rate |
| `attribute.size` | File size in bytes; verified against the file on disk |
| `attribute.duration` | Duration in **milliseconds** |
| `annotation[]` | Task-dependent, see below |

Annotation shape by task:

- ASR and readback tasks — `annotation[0].transcription.text`, a string or a list
  of strings.
- VAD and other segmented sources — `annotation[].timestamp.{begin_time,end_time}`
  (seconds) for each speech segment.

### `ds.jsonl` metadata

- `audio.speech.language` — e.g. `zh`.
- `supported_tasks` — top-level, or tolerated at `audio.speech.supported_tasks`.
  This, not a user-supplied flag, selects the projection task. Absent means the
  legacy ASR default; declared but unsupported is an error.

## OpenBench — the publication format

```text
<dataset_name>/
├── README.md      # YAML front matter + Markdown description
├── sample.jsonl
└── audio/         # or any directory the records point at
```

Official source: [dataset construction spec](https://www.open-bench.net/docs/dataset/).

### `sample.jsonl` record

Short audio:

```json
{"key":"abc00000001","path":"audios/1998-29454-0041.wav","text":"导航去北京","custom":{}}
```

Long audio adds one row per segment:

```json
{"key":"abc00000001","path":"audios/meeting001.wav","text":"大家上午好今天开始会议","start_time":0.0,"end_time":12.5,"speaker_id":"sp001","segment_id":"meeting001_000001","custom":{}}
{"key":"abc00000002","path":"audios/meeting001.wav","text":"首先介绍一下项目背景","start_time":12.5,"end_time":25.8,"speaker_id":"sp002","segment_id":"meeting001_000002","custom":{}}
```

- Short audio: `{key, path, text}` — all three required and all strings.
- Long audio: adds `start_time` and `end_time`, which must appear together, be
  numeric, and satisfy `end_time > start_time`. `speaker_id` and `segment_id` are
  optional.
- `path` is **relative to the dataset root**, must not be absolute, and must not
  contain `..`.
- `key` must be unique across the file. Records must be UTF-8, one JSON object
  per line, with no comments, blank lines, or trailing commas. The upstream
  long-audio example has a JSON comma typo; legal JSON wins.

### README.md front matter

`language`, `supported_tasks`, `type`, and `license` are required; everything
else is optional. Array tags accept a YAML list or a single scalar; prefer a list
for multi-valued tags.

| Tag | Required | Accepted values |
| --- | --- | --- |
| `language` | yes | Platform language codes, e.g. `zh`, `en`, `ja`, `ko`, `es`, `fr`, `de`, `ru`, `pt` |
| `supported_tasks` | yes | `ASR`, `TTS`, `WakeUp`, `FalseTrigger`, `SP`, `DOA`, `LID`, `other` |
| `type` | yes | `audio`, `text`, `image`, `video` |
| `license` | yes | The actual license, e.g. `apache-2.0` |
| `source_info` | no | Free-text provenance |
| `speech_style` | no | `add`, `conv`, `unknown` |
| `environment` | no | 商超卖场、直播间带货、录音棚录制、医疗诊室、会议室开会、隧道驾驶、银行政务大厅、地铁有轨电车、室外安静、室内安静、厨房烹饪、未知、公众展会、车内驾驶、室内带噪、车内安静、地铁机场站台、高速驾驶 |
| `distance` | no | `near`, `far` |
| `array_info` | no | Free-text microphone-array description |
| `genre` | no | 对话、娱乐、采访、唱歌、戏剧、电影、视频博客、直播、演讲、剧集、朗诵、广告、动物叫声、枪声、未知 |
| `device` | no | `phone`, `hifi`, `ondevice`, `array`, `other`, `unknown` |
| `background` | no | `quiet`, `noisy`, `mix` |
| `dialect` | no | Platform accent codes, e.g. `putonghua`, `beijing`, `yue`, `en-US`, `en-UK` |
| `noise_style` | no | 自然人声、自然环境音、清晰合成音、机械/机器人音、伪影/电音失真、未知 |
| `info` | no | Free-text supplement |
| `channels` | no | Integer `1` to `16` |
| `sample_rate` | no | `8000`, `16000`, `22050`, `23000`, `24000`, `32000`, `44100`, `48000`, `96000` |
| `tag` | no | `speech`, `noise`, `music`, `audio_event`, `echo`, `other` |
| `anno_method` | no | `manu`, `other` |
| `source` | no | Free-text source |
| `generation` | no | `record`, `real`, `synthetic`, `augment`, `other` |

The machine-checkable subset of this table is the five constants at the top of
`scripts/validate_openbench_dataset.py` (`TASKS`, `TYPES`, `ENUMS`,
`SAMPLE_RATES`, `TAGS`) plus the `channels` range. The validator does **not**
check that a `language` value is real — only that the tag is present — nor does
it check `environment`, `genre`, `dialect`, `noise_style`, or free-text tags, so
passing validation is not proof those are right.

Do not invent a value to get past validation. If the source pool has no
corresponding information, leave the tag unset.

## Field mapping: `ds_pool` → OpenBench

| OpenBench | Source | Notes |
| --- | --- | --- |
| `key` | `sample_id` | Falls back to the media file stem when absent. The projector already rejects duplicate keys, so the only residual risk is rows that omit `sample_id` and share one media file |
| `path` | `attribute.path` | **Re-base**: the source path is relative to `raws/sample/`, the OpenBench path to the dataset root |
| `text` | `annotation[0].transcription.text` | Source-side is an array, OpenBench wants a string — join with spaces. For readback tasks (TTS, KWS) this is the reference text the audio contains, not a synthesis request |
| `start_time` / `end_time` | `annotation[].timestamp.{begin_time,end_time}` | Seconds; meaningful only for segmented sources |
| README `language` | `ds.jsonl` `audio.speech.language` | A human check — the validator only requires the tag to be present, not that the code is valid |
| README `supported_tasks` | `ds.jsonl` `supported_tasks` | Vocabularies differ — see the mapping table below |
| README `type` | — | `["audio"]` for a speech pool |
| README `license` | **absent from the source pool** | A human decision. Ask the owner; never guess or fill a placeholder |
| `sample_rate` | `attribute.sample_rate` | Must be one of the fixed platform rates; anything else fails validation |
| `channels` | `attribute.channels` | Must be 1 to 16 |
| `speech_style`, `environment`, `distance`, `array_info`, `genre`, `device`, `background`, `dialect`, `noise_style`, `tag`, `anno_method`, `source`, `generation`, `source_info`, `info` | **not present in the source pool** | All optional. Leave unset rather than inventing |

The projector also writes `parent_sample_id`, `raw_data_md5`,
`raw_data_format`, `size`, `channels`, `version_id`, and `source_dataset_name`
into the projection `metadata`. None has an OpenBench home: keep them in
`custom` or drop them.

### Task vocabulary

Only two source task ids map onto OpenBench's `supported_tasks` with certainty:

| Source task | OpenBench `supported_tasks` | Certainty |
| --- | --- | --- |
| `ASR` | `ASR` | Confirmed |
| `TTS` | `TTS` | Confirmed |
| `KWS` | `WakeUp` | **Unverified** — KWS is keyword spotting, WakeUp is wake-word detection; close but not the same |
| `VAD`, `SD`, `SE`, `SER`, `SLU`, `SV`, `TSE`, `VC`, `GR`, `S2TT`, `SA-ASR`, `CLASSIFICATION` | `other` | The platform vocabulary has no corresponding value |

`SP`, `DOA`, `LID`, and `FalseTrigger` exist in the platform vocabulary but no
source pool produces them, so they are not mappings. Prefer `other` over forcing
a match: one wrong word makes the dataset wrong for every consumer. Do **not**
assume the task ids pass through unchanged: `VAD` is a legal source task that
projects to `vad_segments_v1`, but the OpenBench README rejects it outright, so
an exported VAD dataset has to be tagged `other`:

```text
ERROR: README.md: unsupported supported_tasks value: VAD
```

## Two structural gaps

Neither is a field rename; both must be resolved before exporting.

1. **Path base differs.** `attribute.path` is relative to `raws/sample/`, while
   OpenBench requires a path relative to the dataset root with the media inside
   it. Renaming alone produces a wall of `does not exist`. Either copy the media
   into the OpenBench root, or set the OpenBench root at `raws/sample/` and
   adjust the README and every relative path to match.
2. **VAD needs a fan-out.** The source side has one row per file with
   `speech_segments` as an array; OpenBench long audio is **one row per
   segment**, sharing one `path`, each with its own `key`. That is one-to-many,
   not a mapping.

## Traps

- Both formats use the filename `sample.jsonl` with incompatible schemas. Never
  run the OpenBench validator against a source-pool `sample.jsonl`, and never
  point the source resolver at an OpenBench file.
- A missing `license` is a hard OpenBench failure and cannot be derived from the
  pool. Ask the owner instead of filling in a placeholder.
- `attribute.size` and `attribute.duration` are source-pool bookkeeping, not
  OpenBench fields; keep them out of converted records.
- Fixed enums (`sample_rate`, `channels` 1–16, `device`, `environment`, …) reject
  values a source pool may legitimately carry. Fix or omit the tag; do not write
  a false value to satisfy the validator.
- The validator checks that referenced media exists by default; pass
  `--skip-media` for a metadata-only check.

## Tooling

`scripts/validate_openbench_dataset.py` is vendored from the `openbench-dataset`
skill (stdlib only, no PyYAML) and checks a converted directory against the
format above. It is listed in `UNIT_AGNOSTIC_SCRIPTS` — it writes no artifact and
carries no state-machine position, so any unit may run it.

```bash
"$HARNESS_PYTHON_BIN" scripts/validate_openbench_dataset.py <converted_dataset_dir>
```

Success prints the record count and `VALID`; failure prints numbered errors on
stderr and exits non-zero. The validator never modifies its input. Do not fork
it — fixes belong upstream.

Publishing is a side-effecting external operation. `ob upload` / `ob release`
must be shown to the user — dataset name, remote path, and version — and
confirmed before they run. The API key is read from `~/.OREF/ds.cfg`; it must
never be written into this repository, printed, or pasted into an artifact, and
an existing `~/.OREF/ds.cfg` must never be overwritten.
