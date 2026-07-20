# Academia Engine Operator Guide

This is the authoritative manual for operating the local episode pipeline. Publishing and social-network upload are manual and outside Academia Engine.

## Safety first

> **Provider generation may consume credits.**

`--generate` without `--confirm` performs safe planning validation and does not submit. `--confirm` is the explicit cost boundary. After an ambiguous submission failure, do not blindly run generation again: inspect durable state and reconcile the existing provider task when necessary.

Never place credentials, authorization headers, provider payloads, or signed download URLs in episode input or durable state. CLI output is intentionally limited to operational identifiers, paths, statuses, and artifact metadata.

## Pipeline and lifecycle

```text
Episode JSON -> DirectorEngine -> DirectorPlan -> PromptBuilder
             -> VideoGenerationRequest -> GenerationRequestStore
             -> EpisodeProductionRequest -> EpisodeProductionOrchestrator
             -> VideoEngine -> Kling/provider -> local scene artifacts
             -> Timeline -> FFmpeg -> final.mp4
```

The recommended lifecycle is:

1. Validate input with `--plan --preflight` (read-only).
2. Persist the plan with `--plan` (writes provider-neutral request records).
3. Generate only with `--generate --confirm` (may consume credits, writes production state, and eventually invokes FFmpeg).
4. Inspect progress with `--status` (read-only).
5. If interrupted, continue the same production with `--resume`.
6. Verify durable scene and final artifacts with `--verify` (read-only).
7. Publish `final.mp4` manually outside Academia Engine if desired.

Prefer `--resume` over creating a new production ID after interruption. Resume uses durable task IDs and completed scene artifacts, but may submit scenes that were never durably submitted.

## Unified CLI reference

All normal operations use:

```text
python -m app.cli.episode OPERATION [arguments]
```

| Operation | Purpose and required arguments | Important optional arguments |
|---|---|---|
| `--plan` | Validate and persist an Episode input. Requires `--input`, `--production-id`, `--scene-output-dir`, `--workspace`, and `--output`. | `--preflight`, `--provider`, `--transition`, `--transition-duration` |
| `--generate` | Validate, persist, and run production. Requires the same planning arguments. | `--confirm`, polling arguments, provider and transition arguments |
| `--status` | Read durable production state. Requires `--production-id`. | None |
| `--resume` | Continue an existing durable production. Requires `--production-id`. | `--interval`, `--timeout`, `--max-attempts` |
| `--verify` | Read and hash-check durable artifacts. Requires `--production-id`. | None |
| `--repair-metadata` | Reconstruct approved metadata for an existing scene file. Requires `--production-id` and `--scene-id`. | None |
| `--cleanup` | Scan or delete allowlisted disposable runtime paths. | `--older-than-hours`; deletion additionally requires `--confirm` |

### Safety matrix

“Provider calls” includes submission, status queries, or downloads. “FFmpeg” does not include FFprobe-only validation.

| Operation | Writes state | Provider calls | FFmpeg | May consume credits |
|---|---:|---:|---:|---:|
| Plan preflight (`--plan --preflight`) | No | No | No | No |
| Persistent plan (`--plan`) | Yes, request records | No | No | No |
| Generate without confirm | No | No | No | No |
| Generate with confirm | Yes | Yes | Yes, after scenes are ready | Yes |
| Status | No | No | No | No |
| Resume | Yes | Possible | Possible | Possible for never-submitted scenes |
| Verify | No | No | No | No |
| Repair metadata | Yes, manifest metadata | No | No | No |
| Cleanup dry-run | No | No | No | No |
| Cleanup confirmed | Deletes allowlisted disposable data | No | No | No |
| Reconcile task | Yes | Yes, task query | No | No new generation submission |
| Recover scene | Yes | Yes, artifact download | No | No new generation submission |
| Attach local scene | Yes, and may copy a file | No | No; FFprobe is used | No |

## CMD.EXE examples

Run these commands from the repository root. CMD.EXE uses `^` for continuation.

### Planning preflight

```bat
python -m app.cli.episode ^
  --plan ^
  --preflight ^
  --input examples\smoke\episode-input.json ^
  --production-id example-001 ^
  --scene-output-dir .runtime\productions\example-001\scenes ^
  --workspace .runtime\media\example-001 ^
  --output .runtime\productions\example-001\final.mp4 ^
  --transition fade ^
  --transition-duration 0.5
```

### Persistent planning

```bat
python -m app.cli.episode ^
  --plan ^
  --input examples\smoke\episode-input.json ^
  --production-id example-001 ^
  --scene-output-dir .runtime\productions\example-001\scenes ^
  --workspace .runtime\media\example-001 ^
  --output .runtime\productions\example-001\final.mp4 ^
  --transition fade ^
  --transition-duration 0.5
```

### Confirmed generation

```bat
python -m app.cli.episode ^
  --generate ^
  --confirm ^
  --input examples\smoke\episode-input.json ^
  --production-id example-001 ^
  --scene-output-dir .runtime\productions\example-001\scenes ^
  --workspace .runtime\media\example-001 ^
  --output .runtime\productions\example-001\final.mp4 ^
  --transition fade ^
  --transition-duration 0.5 ^
  --interval 2 ^
  --timeout 900
```

### Inspect, resume, and verify

```bat
python -m app.cli.episode --status --production-id example-001

python -m app.cli.episode ^
  --resume ^
  --production-id example-001 ^
  --interval 2 ^
  --timeout 900

python -m app.cli.episode --verify --production-id example-001
```

### Repair scene metadata

```bat
python -m app.cli.episode ^
  --repair-metadata ^
  --production-id example-001 ^
  --scene-id scene-0001

python -m app.cli.episode --verify --production-id example-001
```

### Cleanup

```bat
python -m app.cli.episode ^
  --cleanup ^
  --older-than-hours 24

python -m app.cli.episode ^
  --cleanup ^
  --older-than-hours 24 ^
  --confirm
```

## PowerShell examples

PowerShell uses a backtick for continuation. Do not use CMD.EXE's `^` in PowerShell.

### Planning preflight

```powershell
python -m app.cli.episode `
  --plan `
  --preflight `
  --input examples\smoke\episode-input.json `
  --production-id example-001 `
  --scene-output-dir .runtime\productions\example-001\scenes `
  --workspace .runtime\media\example-001 `
  --output .runtime\productions\example-001\final.mp4 `
  --transition fade `
  --transition-duration 0.5
```

### Persistent planning

```powershell
python -m app.cli.episode `
  --plan `
  --input examples\smoke\episode-input.json `
  --production-id example-001 `
  --scene-output-dir .runtime\productions\example-001\scenes `
  --workspace .runtime\media\example-001 `
  --output .runtime\productions\example-001\final.mp4 `
  --transition fade `
  --transition-duration 0.5
```

### Confirmed generation

```powershell
python -m app.cli.episode `
  --generate `
  --confirm `
  --input examples\smoke\episode-input.json `
  --production-id example-001 `
  --scene-output-dir .runtime\productions\example-001\scenes `
  --workspace .runtime\media\example-001 `
  --output .runtime\productions\example-001\final.mp4 `
  --transition fade `
  --transition-duration 0.5 `
  --interval 2 `
  --timeout 900
```

### Inspect, resume, verify, and repair

```powershell
python -m app.cli.episode --status --production-id example-001

python -m app.cli.episode `
  --resume `
  --production-id example-001 `
  --interval 2 `
  --timeout 900

python -m app.cli.episode --verify --production-id example-001

python -m app.cli.episode `
  --repair-metadata `
  --production-id example-001 `
  --scene-id scene-0001
```

### Cleanup

```powershell
python -m app.cli.episode `
  --cleanup `
  --older-than-hours 24

python -m app.cli.episode `
  --cleanup `
  --older-than-hours 24 `
  --confirm
```

## Administrative recovery runbooks

These specialized CLIs are recovery tools, not the normal happy path.

### Orphan provider task

Failure class: the provider accepted a task and may have consumed credits, but the local production boundary failed before its task ID was durably attached.

1. Do **not** resubmit or start a replacement generation.
2. Find the provider task ID using the provider's external records.
3. Verify and attach it:

   ```bat
   python -m app.cli.episode_reconcile_task ^
     --production-id example-001 ^
     --scene-id scene-0001 ^
     --task-id PROVIDER_TASK_ID
   ```

   This queries the provider and updates the production manifest; it does not submit a new task.

4. Inspect with `python -m app.cli.episode --status --production-id example-001`.
5. If the attached task succeeded but its artifact is absent locally, download only that scene:

   ```bat
   python -m app.cli.episode_recover_scene ^
     --production-id example-001 ^
     --scene-id scene-0001
   ```

6. Resume with `python -m app.cli.episode --resume --production-id example-001`.

### Existing local scene artifact

Use this only when a valid scene video already exists locally and should be attached without fabricating provider state:

```bat
python -m app.cli.episode_attach_local_scene ^
  --production-id example-001 ^
  --scene-id scene-0001 ^
  --input C:\media\recovered-scene.mp4
```

The tool validates with FFprobe, hashes the video, safely copies it to the deterministic scene destination when necessary, and marks the scene ready. It refuses to overwrite provider-backed scenes. Resume afterward so assembly can continue.

### Missing artifact metadata

`--verify` is strictly read-only. If it reports `metadata_missing` for a scene whose durable local file exists, use `--repair-metadata`. Repair reads and hashes that file and updates only approved artifact metadata; it does not submit, query, download, or rerender. Run `--verify` again afterward.

## Cleanup and retention

Cleanup is dry-run by default. `--cleanup --older-than-hours N` only lists old, allowlisted disposable paths and recoverable bytes. Deletion requires both an age threshold and `--confirm`.

Eligible patterns are atomic-writer `.part` files under approved runtime subtrees, isolated `assembly-*` directories, and known `smoke-*` media workspaces. Canonical-path and durable-reference checks are repeated before deletion. Arbitrary runtime paths are not disposable merely because they are under `.runtime`.

## Durable state layout

```text
.runtime/
  productions/       production manifests, scene MP4s, and final MP4s (durable)
  requests/           provider-neutral generation request records (durable)
  kling/tasks/        provider task records (durable)
  kling/videos/       downloaded provider artifacts (retain when referenced)
  media/              rendering workspaces; only allowlisted temporary paths are disposable
```

Provider task IDs and normalized state are durable. Provider credentials, HTTP payloads, Authorization headers, and signed URLs are not.

## Production IDs

Production IDs must begin with a lowercase letter or digit and contain only lowercase letters, digits, `_`, or `-`. They must be unique for new production runs. Do not reuse an existing ID for different generation input; use status and resume for existing production state. Deterministic request references are derived from the production ID, for example `example-001-scene-0001`.

## Final artifact expectations

The current Academia normalization profile is 1280×720, 30 FPS, H.264 through `libx264`, and `yuv420p`. AAC is the configured audio codec when the timeline has audio. Audio presence follows the validated source set: video-only scenes produce a video-only final artifact, and the pipeline does not fabricate an audio track.

The final artifact is published atomically at the configured `--output` path and includes durable byte size, SHA-256, and probed media metadata.

## Lyrics generation

The `deterministic` lyrics generator is local test/development logic and is not AI. It needs no external credentials and does not require confirmation.

The `openai` generator performs real structured AI generation through the OpenAI Responses API. It requires `OPENAI_API_KEY`, optionally accepts a model through `OPENAI_LYRICS_MODEL`, requires explicit `--confirm`, and may incur API costs. Never place the key directly in commands or JSON files.

```bat
python -m app.cli.song_generate_lyrics ^
  --brief examples\smoke\song-brief.json ^
  --generator openai ^
  --output .runtime\songs\counting-1-to-5\lyrics-openai.json ^
  --confirm
```

Omit `--show` to keep full lyrics out of console output. The durable output contains only the provider-neutral `LyricsPlan`, never request headers, provider responses, credentials, or internal instructions.

## Music provider status

The intended complete-song integration is the explicitly named third-party
gateway `sunoapi_org`, documented by `docs.sunoapi.org`. It is not the official
Suno Platform API. See `docs/MUSIC_PROVIDER_SUNO.md` for the exact distinction
and contract.

Configure `SUNOAPI_ORG_API_KEY`, `SUNOAPI_ORG_CALLBACK_URL`, and optionally
`SUNOAPI_ORG_MODEL` (default `V4_5`) and `SUNOAPI_ORG_BASE_URL`. Generation
requires confirmation and consumes gateway credits:

```bat
python -m app.cli.music_generate ^
  --lyrics .runtime\songs\counting-1-to-5\lyrics-openai.json ^
  --music-plan examples\smoke\music-plan.json ^
  --provider sunoapi_org ^
  --output .runtime\music\counting-1-to-5.mp3 ^
  --interval 5 ^
  --timeout 900 ^
  --confirm
```

Each request produces two MP3 songs. Both are preserved in the provider task
result, but the engine intentionally rejects automatic download because it
requires one primary artifact. No automatic selection is made in this sprint.
The durable task can be refreshed without resubmitting:

```bat
python -m app.cli.music_engine_task ^
  --provider sunoapi_org ^
  --task-id PROVIDER_TASK_ID ^
  --refresh
```

Submission is never retried. If its outcome is ambiguous, check the gateway
account history before another request; the provider may already have created
a paid task.

Mureka is retained as an isolated evaluation adapter and is not the intended
production provider or an automatic fallback.

### Legacy evaluation integration (Mureka)

The `mureka` adapter uses Mureka's official asynchronous v1 song API ([quickstart](https://platform.mureka.ai/docs/en/quickstart.html), [submit operation](https://platform.mureka.ai/docs/api/operations/post-v1-song-generate.html), [query operation](https://platform.mureka.ai/docs/api/operations/get-v1-song-query-%7Btask_id%7D.html)). Configure `MUREKA_API_KEY`; optionally set `MUREKA_MUSIC_MODEL` (default `auto`) and `MUREKA_TIMEOUT_SECONDS` (default `30`). Never put credentials in JSON or command arguments.

```bat
python -m app.cli.music_generate ^
  --lyrics examples\smoke\lyrics-plan.json ^
  --music-plan examples\smoke\music-plan.json ^
  --provider mureka ^
  --output .runtime\music\counting-1-to-5.wav ^
  --interval 5 ^
  --timeout 900 ^
  --confirm
```

Generation can consume credits and requires `--confirm`. The adapter submits one song without submission retries, polls by provider task ID, and accepts only the officially documented `wav_url`. Signed URLs, lyrics, prompts, and credentials never enter the task registry. After an ambiguous submission failure, do not submit again; first inspect the provider console and durable task state.

Sprint 13.4 supports WAV only. The generic song URL's encoding is not assumed, FLAC is outside the existing durable audio contract, and no transcoding is performed. Style, mood, instrumentation, vocal direction, tempo, and target duration are represented in Mureka's documented free-form `prompt`; Mureka does not expose those as separate v1 song fields. External-correlation lookup is unavailable because the official contract documents lookup by task ID only.

Resume a known durable task without submitting:

```bat
python -m app.cli.music_engine_task ^
  --provider mureka ^
  --task-id PROVIDER_TASK_ID ^
  --resume ^
  --download .runtime\music\counting-1-to-5.wav ^
  --interval 5 ^
  --timeout 900
```

## Version readiness checklist

- [ ] Input preflight passes
- [ ] Production ID is unique
- [ ] Provider credentials configured
- [ ] Credit-consuming generation explicitly confirmed
- [ ] Production reaches `succeeded`
- [ ] Integrity verification passes
- [ ] `final.mp4` exists
- [ ] Manual publication completed externally if desired
