# Academia Engine

Academia Engine turns provider-neutral preschool Episode JSON into durable generated scenes and an atomically published `final.mp4`.

## Quick start

Validate the included smoke episode without provider calls or durable writes:

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

Provider generation may consume credits and requires explicit `--confirm`. Publishing remains manual and outside this project.

See the authoritative [Operator Guide](docs/OPERATOR_GUIDE.md) for the complete lifecycle, CMD.EXE and PowerShell commands, recovery runbooks, durable-state layout, integrity repair, and safe cleanup procedures.
## Durable project orchestration

The project orchestrator coordinates the existing episode, lyrics, music, and
audio/video composition services without replacing their provider or media
logic. A confirmed project run persists prompt-free coordination state under
`.runtime/projects/<project-id>/project.json`; resume uses that state to skip
completed stages and continue known provider tasks.

```bat
python -m app.cli.project_generate ^
  --episode examples\smoke\episode-input.json ^
  --project-id counting-1-to-5 ^
  --video-provider kling ^
  --lyrics-provider openai ^
  --music-provider sunoapi_org ^
  --output .runtime\projects\counting-1-to-5 ^
  --confirm

python -m app.cli.project_resume --project-id counting-1-to-5
```

Without `--confirm`, `project_generate` performs planning/preflight only and
makes no provider submission.
