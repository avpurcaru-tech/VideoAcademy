# Suno production provider capability report

Research date: 2026-07-21

## Decision

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
contract only. Integrating it would require an explicit decision to trust and
contract with that third-party operator; it is not the selected production path.

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

No real API calls were made during this investigation.
