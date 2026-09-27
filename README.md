# open-mem

**A memory layer that shows its work.**

Put anything in — documents, spreadsheets, email, calendars, audio, video, a
GitHub repository — and ask questions in plain language. Every answer comes back
with the passages it rests on.

And with the part almost nothing else will tell you: **what it left out, and
why.**

---

## Why that second part matters

Ask any system a question and it gives you an answer. You cannot tell whether it
searched everything and found little, or searched almost nothing and found all
of it. Those look identical, and only one of them is worth trusting.

```jsonc
POST /api/v1/ask   {"question": "what did we promise Acme about SSO"}

{
  "answer": "SSO was promised for Q3 [1], later moved to Q4 [2].",
  "citations": [
    {"marker": 1, "text": "…we'll have SSO ready for Q3…", "data_id": "data_01J…"},
    {"marker": 2, "text": "…slipping SSO to Q4…",          "data_id": "data_01K…"}
  ],
  "excluded": [
    {"data_id": "data_01M…", "reason": "not_yet_enriched"},
    {"data_id": "data_01N…", "reason": "access"}
  ]
}
```

Two records were not considered. One had not been processed yet; one you are not
allowed to see. Neither is a bug, and both change how much the answer is worth —
which is why they are in the response rather than in a log.

The same honesty runs through the rest: a deletion issues a certificate you can
check, an answer records which model produced it, and a record that could not be
read says so instead of arriving empty.

---

## Try it

```bash
cd api && docker compose up -d && uv run uvicorn open_mem.app:app --reload
cd ui  && npm install && npm run dev
```

Then open the console on `localhost:3000`, add a file, and ask it something.
Running the whole stack locally needs no cloud account, model key or billing —
[`docs/usage/local.md`](docs/usage/local.md) has the details, including the one
setting that will trip you up.

---

## Reading further

| | |
|---|---|
| [usage](docs/usage.md) | Six scenarios against a running system |
| [architecture](docs/architecture.md) | How it is put together, and why |
| [limits](docs/limit.md) | Every ceiling on the way in, per kind of input |
| [design principles](docs/design-principles.md) | The rules the code is held to |
| [docs](docs/README.md) | Everything, in reading order |

The console's sign-in page reports what this build can actually do, counted from
the running code rather than written down — so a format that stops working stops
being claimed.
