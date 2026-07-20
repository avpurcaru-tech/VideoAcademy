# Suno production provider capability report

Research date: 2026-07-21

## Decision

The selected integration for complete-song generation is now the explicitly
named **third-party** provider `sunoapi_org`, documented at
`https://docs.sunoapi.org/`. It is not the official Suno Platform API. The
official platform assessment below is retained so the two services cannot be
confused.

### Third-party gateway contract

- Operator: the separate sunoapi.org gateway service
- Authentication: `Authorization: Bearer`, configured only through
  `SUNOAPI_ORG_API_KEY`
- Base URL: `https://api.sunoapi.org` (`SUNOAPI_ORG_BASE_URL` may override it)
- Submit: `POST /api/v1/generate`
- Query: `GET /api/v1/generate/record-info?taskId=...`
- Required callback: `SUNOAPI_ORG_CALLBACK_URL` (HTTPS)
- Model: `SUNOAPI_ORG_MODEL`, default `V4_5`; accepted documented identifiers
  are `V4`, `V4_5`, `V4_5PLUS`, `V4_5ALL`, `V5`, and `V5_5`
- Custom supplied lyrics: `customMode=true`, `instrumental=false`, with the
  flattened lyrics in `prompt`
- Semantic direction: deterministic text in the documented `style` field
- Statuses: `PENDING`; processing states `GENERATING`, `TEXT_SUCCESS`, and
  `FIRST_SUCCESS`; `SUCCESS`; and documented failure states
- Results: exactly two `response.sunoData` entries, each using `id` and
  `audioUrl`; the documented examples are MP3 and map to `audio/mpeg`

Both paid outputs are exposed as transient artifacts. The engine never chooses
one automatically. After generation it reports a successful, durable task with
selection required. Operators list safe one-based variants (`1`, `2`) and then
explicitly download one. The selected artifact ID, local path, size, hash, and
content type become durable; neither selected nor unselected signed URLs do.

```bat
python -m app.cli.music_engine_task ^
  --provider sunoapi_org ^
  --task-id PROVIDER_TASK_ID ^
  --variants

python -m app.cli.music_engine_task ^
  --provider sunoapi_org ^
  --task-id PROVIDER_TASK_ID ^
  --select-variant 2 ^
  --download .runtime\music\selected-song.mp3
```

The generic `download()` operation remains strict and rejects multiple outputs.
Resume before selection reports that selection is required; resume after an
explicit download returns the durable artifact without querying or submitting.

Submission is never retried. After an ambiguous failure, inspect gateway
account history before submitting again. Signed audio URLs, lyrics, style,
payloads, and credentials are not persisted.

## Official Suno Platform assessment

The intended production integration is the API operated by **Suno, Inc.** at
`https://platform.suno.com/`. The public landing page identifies the service as
the Suno API, describes a REST API for generating songs, covers, and mashups,
and carries a Suno, Inc. copyright notice.

No `SunoMusicProvider` is implemented yet. The official technical contract is
not publicly visible without signing in, and the public landing page is not
sufficient to safely implement the existing `MusicProvider` operations.

Before implementation, obtain authorized access to Suno Platform and archive
or record its official API reference for the account. The following fields must
be confirmed directly from that reference:

| Contract item | Current result |
|---|---|
| API operator | Suno, Inc. |
| Official source | `https://platform.suno.com/` |
| Authentication | Not documented on the public landing page |
| API base URL | Not documented publicly |
| Submit method and endpoint | Not documented publicly |
| Supplied-lyrics/custom mode | Not confirmed by the public landing page |
| Vocal generation from supplied lyrics | Not confirmed publicly |
| Asynchronous task ID | Not documented publicly |
| Query endpoint or callback | Not documented publicly |
| Status vocabulary | Not documented publicly |
| Artifact fields | Not documented publicly |
| Audio format | Not documented publicly |
| API pricing and credit consumption | Not documented publicly |
| Romanian lyrics support | Not documented publicly |

The next implementation sprint must not infer these values from Suno's consumer
web application, browser traffic, community projects, or third-party gateways.
It may implement `SunoMusicProvider(MusicProvider)` only after the official
Suno Platform contract confirms supplied lyrics, singing vocals, task lifecycle,
and a downloadable audio artifact compatible with the project.

## `docs.sunoapi.org` classification

`https://docs.sunoapi.org/` is **not** the official Suno, Inc. developer API.
It documents a separately operated third-party service with its own domain,
API host (`https://api.sunoapi.org`), API-key management, billing/credits, and
support address (`support@sunoapi.org`). Its documented endpoints and schemas
must not be treated as Suno's official contract.

That third-party gateway documents capabilities relevant to this project,
including custom generation, supplied lyrics, vocals, asynchronous task IDs,
callbacks/polling, and downloadable audio. Those facts describe the gateway's
contract only. Sprint 13.4.2 explicitly selects this third-party contract; this
does not make it an official Suno, Inc. API.

## Unofficial implementations

Community wrappers that automate Suno's consumer site or reverse-engineer its
private endpoints are out of scope. They must not be used for production,
credential handling, schema discovery, or as evidence for the official API
contract.

## Existing Mureka adapter

The Mureka adapter remains isolated for compatibility and evaluation. It is not
the intended production music provider and is not a fallback for Suno. No
provider is automatically selected: Mureka remains available only when an
operator explicitly requests `--provider mureka`.

## Required next step

1. Obtain an authorized Suno Platform account or partner invitation.
2. Export or review the official API reference supplied by Suno.
3. Confirm every contract item in the table above, including Romanian support.
4. Review API pricing, commercial-use terms, retention, and orphan-submit risk.
5. Implement a provider-specific adapter without changing `MusicProvider` or
   `MusicEngine`.

No real API calls were made during this integration or its automated tests.
