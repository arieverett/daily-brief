# Daily Brief

Two personal morning newsletters: an English Sweden + Indonesia edition and a Bahasa Indonesia
edition for Ari's mom. They collect recent reporting, deduplicate overlapping coverage, ask an AI
editor to prioritize the news, run deterministic editorial quality gates, render responsive HTML
and plain-text editions, and send through Resend.

## What ships

- Sweden and Indonesia sections with one lead, concise explainers, and speed reads
- Morning Brew-style writing: direct, conversational, data-forward, and high signal
- Editorial preference for significance, authoritative sourcing, and economic/political context
- Optional bullets that must add new information instead of restating the summary
- Stricter Indonesia bullet filtering, with priority for unused counts, percentages, and currency data
- Exact, validated source links on every item
- Publisher article images when they can be fetched within the image time budget
- Duplicate-send protection through a content-based Resend idempotency key
- Responsive HTML email plus a plain-text fallback
- Automated tests, linting, and a no-API sample renderer

## Production schedule

Both newsletters are scheduled for **3:00 AM America/New_York**. GitHub Actions runs the UTC
variants for daylight and standard time, then a timezone guard selects the correct run.

The scheduled workflows need these repository secrets:

| Secret | Value |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key with billing enabled |
| `RESEND_API_KEY` | Resend sending API key |
| `BRIEF_TO_EMAIL` | Ari's destination email address |
| `INDONESIA_BRIEF_TO_EMAIL` | Recipient for Nusantara Daily |
| `BRIEF_FROM_EMAIL` | Verified sender, e.g. `Daily Brief <news@dailybrief.example.com>` |

The model can be changed with the `OPENAI_MODEL` repository variable. Production currently falls
back to `gpt-5-mini` when the variable is not set.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Export the variables from `.env`, then:

```bash
# Render the bundled standard design sample without network/API keys
python -m briefing --sample

# Generate a live standard edition without sending
python -m briefing

# Generate and send the standard edition
python -m briefing --send

# Generate and send Nusantara Daily
python -m briefing --edition indonesia --sources config/indonesia_sources.yml --send
```

Generated HTML and text files are written to `out/`.

## Pipeline

1. **Collect:** pull recent stories from the configured feeds concurrently.
2. **Normalize:** clean titles, enforce trusted-source lists, and collapse near-duplicate coverage.
3. **Recover:** aim for at least three fresh candidates per relevant country. If the editorial pool
   is thin, expand the lookback to 7 days and then 30 days rather than failing just because the
   current news cycle is light.
4. **Edit:** send the candidate metadata to OpenAI using strict structured output. The model chooses
   the lead, secondary stories, speed reads, setup, subject, and preview text.
5. **Validate:** snap every generated URL back to a real candidate, reject invented sources, merge
   duplicate topics, remove recap bullets, and backfill speed reads from unused candidate topics
   when needed.
6. **Enrich:** fetch publisher social images concurrently under a fixed time budget, with RSS images
   as the fallback.
7. **Render:** build one responsive email template and the edition-specific plain-text fallback.
8. **Send:** deliver through Resend with an idempotency key derived from the edition content.

## Code map

- `src/briefing/collect.py` — feed collection, deduplication, Google News URL resolution, images
- `src/briefing/editor.py` — OpenAI request orchestration and date/localization prompt setup
- `src/briefing/editorial.py` — schemas, newsroom style guide, source validation, quality gates
- `src/briefing/models.py` — newsletter data model and backward-compatible fixture parsing
- `src/briefing/render.py` — cached Jinja template rendering and plain-text output
- `src/briefing/send.py` — recipient parsing and Resend delivery
- `src/briefing/templates/newsletter.html` — email-safe responsive presentation
- `config/sources.yml` — standard Sweden + Indonesia source discovery
- `config/indonesia_sources.yml` — Nusantara Daily source discovery

## Editorial guardrails

- Facts must come from candidate metadata. The model may synthesize coverage, not invent details.
- The news event is the unit of a story; multiple outlet URLs about the same event do not get
  multiple newsletter slots.
- Main-story bullets are optional. **Zero bullets is better than a redundant bullet.**
- Indonesia bullets receive an additional post-generation novelty check. Reused paragraph numbers
  and paraphrased recap bullets are removed automatically.
- Speed reads must use distinct topics and link to the underlying article.
- Source names live in the source row, not in prose such as "according to Reuters."
- Serious stories stay serious. Light wordplay is reserved for appropriate topics.

## Reliability notes

- Feed failures are tolerated individually.
- The editor needs at least six candidate stories per relevant country after fallback so it can fill
  the minimum structured edition without recycling topics.
- Image failures never block delivery; the newsletter can send with RSS images or no image.
- The GitHub test workflow runs Ruff, Pytest, and a full sample render on every push.
