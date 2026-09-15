# Migrating from Open WebUI

velox-ui imports chats from Open WebUI's own export, not from its database file
directly — the export is a stable, documented JSON shape, while the database schema is
an implementation detail that has changed across Open WebUI releases. This covers
chats, their branching structure, tags, model labels and (given a folder export)
folder names. It does not cover accounts, RAG collections, prompts or settings — see
[What is not imported](#what-is-not-imported).

## Export from Open WebUI

In Open WebUI: **Settings -> Chats -> Export**, or, as an administrator, **Admin
Panel -> Database -> Export All Chats (All Users)** for a full-instance export. Either
produces a JSON file, typically named `all-chats-export.json` or similar. Optionally
also export **Settings -> Chats -> Folders** if chats are organized into folders —
Open WebUI's chat export carries a `folder_id` per chat but not the folder's name, so
without this second file imported chats keep their id-only reference and land unfiled.

## Import into velox-ui

```bash
velox import openwebui all-chats-export.json --user alice@example.com
velox import openwebui all-chats-export.json --user alice@example.com --folders folders.json
```

`--user` is the address of the velox-ui account the chats are imported into — an
existing account, since the import does not create one. An administrator's Open WebUI
"export all users" file mixes every user's chats into one JSON array; the importer
does not attribute rows back to their original Open WebUI author, so a multi-user
migration means splitting that file per person (or importing all of it once per
account and deleting what does not belong) before running the command once per user.

The command prints how many chats were imported and, for each one skipped, why:

```
imported 41 chat(s)
  skipped chat-9f2a (''): no messages
```

Running the same export again is a no-op: every imported chat records its Open WebUI
id, and a chat whose id has already been imported for that user is skipped rather than
duplicated. This makes it safe to re-run after fixing a `--folders` file, or to import
an export that is a superset of one already brought in.

## What is imported

- **Chat title, timestamps, pin and archive state.**
- **The full branching message tree** (ADR-0006) — Open WebUI's `history.messages` is
  a parent-linked tree in the same shape velox-ui itself uses internally, so this is a
  structural copy, not a reconstruction. Older Open WebUI exports that only carry a
  flat `messages` array (no `history`) are imported as a single linear branch.
- **Tags**, created or reused by name on the target account.
- **Folder placement**, when `--folders` is given; otherwise chats import unfiled.
- **Model labels**, best-effort: an Open WebUI model id such as `llama3:8b` becomes
  `ollama:llama3:8b`, and ids that look like a known cloud family (`gpt-*`, `claude-*`,
  `gemini-*`, `mistral-*`) are labeled for that provider instead. This is a display
  label only — it never selects a live backend — and is corrected the same way any
  chat's model is changed, from the model picker, if the guess is wrong.

## What is not imported

- **Accounts, passwords and sessions.** Create accounts in velox-ui first (or invite
  the account holders); import chats into that account with `--user`.
- **RAG knowledge bases, documents and citations**, prompts/presets, and function or
  tool configuration. These are configured concepts in Open WebUI with no
  guaranteed-stable export shape; recreate them in velox-ui directly.
- **Attachments and generated images** embedded in messages. Text content imports;
  binary attachments referenced by it do not, and the reference in the imported
  message text will point at a file that no longer exists.
- **Message-level token counts, cost and timing metrics.** These are the imported
  data's own artifacts of *velox-ui's* real-time accounting, not Open WebUI's, so they
  are left unset rather than filled with numbers that describe a different pipeline.

## Troubleshooting

**`no account with email '...'`** — the `--user` address does not match an existing
velox-ui account. Create the account (or have the person sign up) before importing.

**`expected a JSON array of chats, or an object with a 'chats' array`** — the file
passed is not a chat export in either shape this command recognizes; check it was
saved from Open WebUI's chat export rather than, for example, a single-chat "Download
as JSON" from a chat's own menu, which uses a different, narrower shape.

**A chat imports with no folder even though `--folders` was passed** — the chat's
`folder_id` was not found in the folders file. Open WebUI's chat and folder exports
are downloaded separately and can drift out of sync (a folder renamed or deleted after
the chat export was taken); re-export both together and try again.
