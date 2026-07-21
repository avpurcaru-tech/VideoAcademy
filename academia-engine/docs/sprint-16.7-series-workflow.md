# Luca și Max official workflow

Register canonical recurring characters first:

```bat
python -m app.cli.character_register ^
  --input examples\official\luca-si-max\characters\luca.json

python -m app.cli.character_register ^
  --input examples\official\luca-si-max\characters\max.json
```

Profiles are stored atomically at `.runtime\characters\<character-id>.json`.
They can be inspected safely with:

```bat
python -m app.cli.character_show --character-id luca
python -m app.cli.character_show --character-id max
```

Then register the durable Series Bible:

```bat
python -m app.cli.series_register ^
  --input examples\smoke\luca-si-max-series-bible.json
```

Run the safe preflight (no provider is constructed or called):

```bat
python -m app.cli.project_generate_from_brief ^
  --brief examples\official\luca-si-max\episode-001-colors-brief.json ^
  --project-id luca-si-max-colors-001 ^
  --episode-generator openai ^
  --video-provider kling ^
  --lyrics-provider openai ^
  --music-provider sunoapi_org ^
  --output .runtime\projects\luca-si-max-colors-001
```

Run confirmed generation with the same command plus `--confirm`. The resolved
`series_id` is stored in `project.json`; resume uses durable project inputs and
does not require the original brief. The canonical Bible remains separately
durable at `.runtime\series\luca-si-max\series-bible.json`. Provider prompts and
provider payloads are not persisted.

Canonical textual profiles materially improve prompt continuity, but cannot
guarantee identical faces across independently generated text-to-video clips.
Higher visual consistency will require canonical reference images and a future
provider workflow supporting image-to-video or character references.
