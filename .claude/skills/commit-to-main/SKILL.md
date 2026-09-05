---
name: commit-to-main
description: How work lands in this repo — straight onto main, no feature branch and no pull request. Invoke before any commit, push, or branch here, whenever asked to ship, land or merge something, and any time a pull request seems like the answer. Overrides the default branch-and-PR habit.
---

# Landing work in this repo

**Commit to `main`. Do not open a pull request.**

This is a single-maintainer repo. A pull request here is a review request
addressed to the person who wrote the code, and the branch it needs exists only
to carry it. Both are ceremony around a step that never happens, and the cost is
not neutral: work sits unmerged, `main` stops being the thing that is true, and
the next session starts by reconstructing which branch had the real version.

This overrides the general "if on the default branch, branch first" habit.
Here, the default branch *is* where work goes.

## The sequence

1. **Be on `main` and current.** `git checkout main && git pull --rebase`.
2. **Make the change and verify it** — see [Nothing catches this but you](#nothing-catches-this-but-you).
3. **`git commit`** to `main`.
4. **Invoke the `changelog` skill** — immediately, before pushing. It is a
   separate rule and this one does not replace it. See
   `.claude/skills/changelog/SKILL.md`.
5. **`git push`.**

Do not run `gh pr create`. Do not create a branch to hold the work. Do not
merge, squash-merge, or ask which of those is wanted — there is nothing to
merge, because the commit was already on `main`.

## What has not changed

**Still only commit when asked.** This skill settles *where* work lands, not
*when*. It is not standing permission to commit and push after every edit; an
uncommitted working tree is still a normal way to end a turn. Wait to be asked,
then land it here.

**Still never force-push `main`.** There is no branch to recover from and no PR
to re-open. A rewritten `main` is the only copy, rewritten.

**If the remote moved, rebase.** `git pull --rebase` before pushing. A merge
commit reconciling `main` with itself is noise in a history nobody branched.

## Nothing catches this but you

There are no GitHub Actions workflows in this repo and no PR gate. Nothing runs
between `git push` and `main` being wrong. Whatever a reviewer would have caught
has to be caught before the commit:

- **API** — `cd api && pytest`
- **UI** — `cd ui && npm run verify` (proxy paths, typecheck, tests, build)

Run the side you touched. Say plainly in the response what you ran and what it
said — a green run stated is the only signal there is, and "tests pass" without
having run them is worse here than in a repo with CI, because there is no second
check behind it.

## When a branch is right anyway

Rare, and it is the user's call rather than a judgement to make silently:

- **They ask for one.** A named branch requested is a branch, no argument.
- **The work is a spike that may be thrown away**, and putting it on `main`
  would mean reverting rather than deleting.
- **A rewrite spanning many commits** that is genuinely unusable half-done.
  Land it in one piece; a broken `main` between commits is the failure this
  repo has no gate to catch.

Anything smaller — a fix, a feature, a doc, a refactor — goes on `main`. If a
branch seems necessary, say why in one sentence and ask, rather than opening one
and reporting it afterwards.

## Branches that already exist

A pre-existing feature branch is not retroactively wrong, and this skill does
not mean abandoning one mid-flight. Finish what is on it, then get it onto
`main` — and say how many commits are involved, because a long-lived branch is
exactly the state this skill exists to stop accumulating.
