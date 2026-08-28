---
name: changelog
description: Record a completed commit in CHANGELOG.md. Invoke immediately after every `git commit` in this repo, before pushing. Also use when asked to update, backfill, or check the changelog.
---

# Changelog

`CHANGELOG.md` is the record of what this repo became, for a reader who was not
here when it happened. It is not `git log` — the log already exists, and
duplicating it is worse than useless because it is longer and less true.

## Invoke this after every commit

The moment a `git commit` succeeds, add its entry. Not at the end of the
session, not before the commit "so it's in the diff" — after, while the reasons
are still in context. A changelog written from a cold reading of the diff loses
exactly the part that made it worth writing.

The entry lands in the working tree as an uncommitted change; it is swept up by
the *next* commit. That one-commit lag is deliberate: it keeps the entry
truthful about what actually shipped, and it means the changelog never has to
describe itself.

## What an entry says

One line per user-visible change, under a heading for the version or the date.
The test for a line: **would someone who has to use, operate, or debug this
learn something from it that the commit subject did not already tell them?**

- **Say what changed for the reader, not what moved in the tree.** Not "refactor
  webhooks.py" — "a webhook from an unregistered provider is now rejected at the
  door rather than stored and dropped later."
- **Behaviour changes and bug fixes always earn a line.** So does anything that
  changes an interface, a default, a status code, or a stored shape.
- **A fix says what was wrong, briefly.** "Ownership followed the producer
  rather than the writer, so a second user could not see their own writes." The
  symptom is the part a reader recognises.
- **Migrations, new environment variables and new required settings are called
  out explicitly**, because those are the lines someone deploying needs.
- **Pure internal churn gets nothing.** Formatting, a test rename, a comment.
  Silence is a valid entry.

Group under `### Added`, `### Changed`, `### Fixed`, `### Removed` — and
`### Migrations` when a commit ships one. Drop any heading with nothing under
it; an empty section is noise.

## Shape

Newest first. `## Unreleased` at the top while work is in flight; when a version
is cut, rename that heading to the version and date and open a fresh
`## Unreleased`.

```markdown
## Unreleased

### Added
- `POST /api/v1/ask` answers a question over retrieved records, citing the
  passage behind each claim.

### Fixed
- A model rate limit surfaced as a 502 with the upstream URL in the body. It is
  now a 429 with `Retry-After`.

### Migrations
- `0019_answers.sql` — `queries.answer_access_level`; run before deploying.
```

## Before writing

Read the tail of the existing `CHANGELOG.md` first, so the entry matches the
voice already there and does not repeat a line that is present. If the file does
not exist, create it with a short header explaining what it records, then
backfill from `git log --reverse` — grouping aggressively, because a backfill is
a summary of an era, not a transcription of it.
