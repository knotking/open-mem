# mem-dog

## After every commit

Invoke the `changelog` skill immediately after each successful `git commit`, and
before pushing. The entry lands uncommitted and is swept into the next commit;
that lag is intended. See `.claude/skills/changelog/SKILL.md`.

## Deploying

Invoke the `deploy-gcp` skill for any deploy to GCP, and again whenever the
deployment process changes — a new env var, secret, step, IAM grant, or a
failure mode not already listed. The skill is the record of how mem-dog is
actually deployed; it is only worth trusting if it is corrected in the same
session the change is discovered. See `.claude/skills/deploy-gcp/SKILL.md`.

## Building UI

Invoke the `console-ui` skill before adding or changing anything in
`ui/components`, and before wiring a new API surface into the console. A screen
that renders is not a finished screen: the test is whether someone can do the
whole job there and see whether it worked. See
`.claude/skills/console-ui/SKILL.md`.
