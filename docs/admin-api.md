# Admin API

Bearer-token API for pushing data without redeploying. Mint a key in
the admin UI, use it from `curl` / n8n / GitHub Actions to import
mods, post news, manage roadmap items, or trigger Steam polls.

The same reference is rendered live (with the current host) at
[`/admin/api-docs`](https://workshopmods.org/admin/api-docs) (admin
cookie required).

---

## Quickstart

```bash
# 1. Sign in to the admin panel and mint a key at /admin/api-keys
#    (label + TTL). Copy the `mbak_…` string ONCE — it's never shown again.
export MODBOARD_KEY="mbak_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# 2. Verify the key works
curl -fsS https://workshopmods.org/api/admin/whoami \
  -H "Authorization: Bearer $MODBOARD_KEY"
# → {"label":"import-script","prefix":"mbak_AbC123","created_at":…,"expires_at":…,"last_used_at":…}
```

---

## Authentication

Every call requires an `mbak_…` bearer token:

```
Authorization: Bearer mbak_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Alt header for clients that can't set Authorization:

```
X-API-Key: mbak_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Keys are minted at `/admin/api-keys`:

- Default TTL **24h**; clamp 1h – 30d (720h).
- SHA-256 hashed at rest; only the first 12 chars (`mbak_xxxxxx…`) are
  ever displayed afterwards.
- Revocable instantly from the same page.
- Mint + revoke logged to `security_events` as `admin_action`.
- `last_used_at` is bumped on every successful authenticated call.

---

## Endpoints

All return JSON. All write endpoints accept `Content-Type: application/json`.
Errors follow:

```json
{"detail": "human-readable reason"}
```

| Status | Meaning |
|--------|---------|
| 401 | Missing / invalid / expired / revoked key |
| 400 | Bad input (wrong type, unknown field, enum violation) |
| 404 | Target not found |
| 409 | Already exists (e.g. mod with that workshop_id) |

### `GET /api/admin/whoami`

Verify a key. Returns its metadata.

```bash
curl -fsS https://workshopmods.org/api/admin/whoami \
  -H "Authorization: Bearer $MODBOARD_KEY"
```

---

### Mods

#### `GET /api/admin/mods`

List every tracked mod.

```bash
curl -fsS https://workshopmods.org/api/admin/mods \
  -H "Authorization: Bearer $MODBOARD_KEY" | jq
```

#### `GET /api/admin/mods/{workshop_id}`

Get one mod by Steam Workshop ID.

#### `POST /api/admin/mods`

Add a mod.

```bash
curl -fsS https://workshopmods.org/api/admin/mods \
  -H "Authorization: Bearer $MODBOARD_KEY" \
  -H "Content-Type: application/json" \
  -d '{"workshop_id": 3724689682, "name": "DayCount"}'
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `workshop_id` | int | ✓ | Steam Workshop file id |
| `name` | string | ✓ | Short slug, ≤64 chars |
| `public` | bool | — | default `true` |
| `workshop_url` | string | — | auto-built from id if omitted |
| `github_url` | string | — | optional |

#### `POST /api/admin/mods/bulk`

Up to **200** mods per call. Returns per-item outcome
(`created` / `exists` / `bad_input`).

```bash
curl -fsS https://workshopmods.org/api/admin/mods/bulk \
  -H "Authorization: Bearer $MODBOARD_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {"workshop_id": 3724689682, "name": "DayCount"},
      {"workshop_id": 3721918079, "name": "LifeMilestones"}
    ]
  }'
```

#### `PATCH /api/admin/mods/{workshop_id}`

Partial update. Allowed fields:
`name`, `title`, `workshop_url`, `github_url`, `thumbnail_url`, `public`.

```bash
curl -fsS -X PATCH https://workshopmods.org/api/admin/mods/3724689682 \
  -H "Authorization: Bearer $MODBOARD_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "Day Count (HUD)", "public": true}'
```

#### `DELETE /api/admin/mods/{workshop_id}`

Cascades to snapshots, comments, changelogs, discussions, roadmap,
and subscriptions.

```bash
curl -fsS -X DELETE https://workshopmods.org/api/admin/mods/3724689682 \
  -H "Authorization: Bearer $MODBOARD_KEY"
```

---

### Steam poll

#### `POST /api/admin/poll`

Queue a Steam scrape cycle. Returns immediately (`202`).

```bash
curl -fsS -X POST https://workshopmods.org/api/admin/poll \
  -H "Authorization: Bearer $MODBOARD_KEY"
# → {"status": "queued"}
# or {"status": "already_running"} if a poll is in progress
```

Coalesces concurrent calls so you can't accidentally spawn 10
scrapers. Useful right after a `mods/bulk` import to populate stats
on the new entries without waiting for the next cycle.

---

### News

#### `GET /api/admin/news`

List newest 100 news posts.

#### `POST /api/admin/news`

```bash
curl -fsS https://workshopmods.org/api/admin/news \
  -H "Authorization: Bearer $MODBOARD_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "ModBoard is live",
    "body": "Welcome! **Bold** and *italic* supported. > quote",
    "kind": "celebration",
    "show_banner": true
  }'
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `title` | string | ✓ | ≤200 chars |
| `body` | string | ✓ | Markdown-lite (same renderer as forum) |
| `kind` | string | — | `info` (default), `update`, `warning`, `celebration` |
| `active` | bool | — | default `true` (false hides from `/news`) |
| `show_banner` | bool | — | default `false`; newest active+banner becomes site-wide strip |

#### `PATCH /api/admin/news/{id}` / `DELETE /api/admin/news/{id}`

Same allowed fields as create.

---

### Roadmap (per-mod)

#### `GET /api/admin/roadmap/{mod_id}`

List a mod's roadmap items in display order.

#### `POST /api/admin/roadmap`

```bash
curl -fsS https://workshopmods.org/api/admin/roadmap \
  -H "Authorization: Bearer $MODBOARD_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "mod_id": 3724689682,
    "title": "Add settings panel",
    "body": "User-tweakable counter limits.",
    "status": "in_progress",
    "position": 1
  }'
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `mod_id` | int | ✓ | must point to an existing mod |
| `title` | string | ✓ | ≤200 chars |
| `body` | string | — | optional long description |
| `status` | string | — | `planned` (default), `in_progress`, `done`, `cancelled` |
| `position` | int | — | lower = higher in list (default 0) |

#### `PATCH /api/admin/roadmap/{item_id}` / `DELETE /api/admin/roadmap/{item_id}`

Allowed fields: `title`, `body`, `status`, `position`.

---

## Recipes

### Import a CSV of mods

`items.csv` with one mod per line: `workshop_id,name`

```bash
#!/usr/bin/env bash
set -euo pipefail
KEY="$MODBOARD_KEY"

items='[]'
while IFS=, read -r id name; do
  items=$(echo "$items" | jq --argjson id "$id" --arg n "$name" \
    '. += [{"workshop_id":$id,"name":$n}]')
done < items.csv

curl -fsS https://workshopmods.org/api/admin/mods/bulk \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d "{\"items\":$items}" | jq

# Trigger poll so the new mods get stats populated
curl -fsS -X POST https://workshopmods.org/api/admin/poll \
  -H "Authorization: Bearer $KEY"
```

### GitHub Actions: daily mod-list sync

```yaml
name: sync-mods
on:
  schedule:
    - cron: "0 6 * * *"
  workflow_dispatch:
jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Push tracked mods
        env:
          MODBOARD_KEY: ${{ secrets.MODBOARD_KEY }}
        run: |
          curl -fsS https://workshopmods.org/api/admin/mods/bulk \
            -H "Authorization: Bearer $MODBOARD_KEY" \
            -H "Content-Type: application/json" \
            --data-binary @mods.json
```

### n8n HTTP Request node

| Setting | Value |
|---------|-------|
| Method | `POST` |
| URL | `https://workshopmods.org/api/admin/mods` |
| Authentication | Header Auth |
| Header name | `Authorization` |
| Header value | `Bearer {{$credentials.modboardKey}}` |
| Body content type | JSON |
| Body | `{"workshop_id": {{$json.id}}, "name": "{{$json.name}}"}` |

---

## Key hygiene

- **One key per use-case.** Separate `n8n-prod`, `local-import`,
  `github-action` so a leak is surgical to revoke.
- **Shortest TTL that fits the job.** 24h is the default; if you're
  running a single import, mint a 1h key.
- **Revoke immediately** if a key leaks (committed to a repo,
  accidentally posted in chat). The status flips to "revoked"
  before the next API call validates.
- **Don't echo keys in CI logs.** Store in `secrets.MODBOARD_KEY`
  / `MODBOARD_KEY` env, never in workflow YAML or script files.
- **Losing the plain key = revoke + re-issue.** Only the SHA-256
  hash is stored — there's no way to recover the original.
