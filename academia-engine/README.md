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
