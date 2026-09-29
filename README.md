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
- Duplicate-send protection through retry-stable Resend idempotency keys
- Responsive HTML email plus a plain-text fallback
- Automated tests, linting, and a no-API sample renderer

## Production schedule

Both newsletters land at **6:00 AM America/New_York, Monday through Saturday**.

| When (ET) | What happens |
|---|---|
| 05:45 | An external scheduler calls the `workflow_dispatch` API for **Morning briefs** (`briefs.yml`). Each edition is generated, then handed to Resend with `scheduled_at` = 06:00, so it arrives at 6:00 on the dot. |
| 05:55 | If a backstop run is waiting, it sends any edition that still has no delivery record. |
| until 15:00 | Hourly backstop runs (`briefs-backstop.yml`) send anything still missing, immediately. After 15:00 a morning brief is too stale to send automatically. |

GitHub's own cron is **not** the primary trigger because it runs very late for this repo: in
September 2026 the 06:07 cron started between 10:21 AM and 2:05 PM and sometimes not at all.
The backstop therefore polls hourly from 8:17 PM the evening before; each poll exits in seconds
unless it lands inside the 05:00–15:00 window.

Every successful automatic send uploads a `delivered-<edition>-<date>` artifact. All runs check
that record before generating, and each edition's sends are serialized, so the primary and
backstop can overlap without sending twice. Resend's per-edition, per-day idempotency key is a
second, independent guard. Sundays are skipped.

If an automatic send fails, Ari gets one alert email per edition per day with a link to the run.

### External trigger setup (once)

1. **Token.** GitHub → Settings → Developer settings → Fine-grained personal access tokens →
   *Generate new token*. Repository access: *Only select repositories* → `daily-brief`.
   Permissions: **Actions: Read and write** (nothing else). Pick the longest expiry you're comfortable
   with and set a calendar reminder to rotate it.
2. **Scheduler.** Create a free account at [cron-job.org](https://cron-job.org) and add a cron job:
   - URL: `https://api.github.com/repos/arieverett/daily-brief/actions/workflows/briefs.yml/dispatches`
   - Schedule: custom, **05:45**, Monday–Saturday, time zone **America/New_York**
   - Advanced → Request method **POST**, headers
     `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`,
     `X-GitHub-Api-Version: 2022-11-28`, request body `{"ref":"main"}`
   - Notifications: turn on *notify on failure* (catches an expired token).
   - Use *Test run* once: GitHub answers **204** and a *Morning briefs* run appears. On a day that
     already went out, that run just logs "already delivered" and skips.

Any scheduler that can send that POST works the same way.

### Manual runs

- **Send whatever hasn't gone out today:** Actions → *Morning briefs* → *Run workflow*.
- **Re-send a revision / test a change:** same, with *force* ticked (sends immediately, doesn't
  count as the day's delivery). The Indonesia edition also goes to its recipient.
- Committing a change to `.github/run-briefs-now` behaves like a normal (unforced) manual run.

### Secrets

The delivery workflows need these repository secrets:

| Secret | Value |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key with billing enabled |
| `RESEND_API_KEY` | Resend sending API key |
| `BRIEF_TO_EMAIL` | Ari's destination email address (also receives failure alerts) |
| `INDONESIA_BRIEF_TO_EMAIL` | Recipient for Nusantara Daily |
| `BRIEF_FROM_EMAIL` | Verified sender, e.g. `Daily Brief <news@dailybrief.example.com>` |

The model can be changed with the `OPENAI_MODEL` repository variable. Production falls back to
`gpt-5-mini` when the variable is not set.

## Run locally

Install [uv](https://docs.astral.sh/uv/), then:

```bash
uv sync --extra dev        # exact versions from uv.lock, plus pytest and ruff
cp .env.example .env
```

Export the variables from `.env`, then:

```bash
# Render the bundled standard design sample without network/API keys
uv run python -m briefing --sample

# Generate a live standard edition without sending
uv run python -m briefing

# Generate and send the standard edition
uv run python -m briefing --send

# Generate and send Nusantara Daily
uv run python -m briefing --edition indonesia --sources config/indonesia_sources.yml --send
```

Generated HTML and text files are written to `out/`.

## Pipeline

1. **Collect:** pull recent stories from the configured feeds concurrently. Google News searches
   are the primary source; direct publisher RSS feeds (`tier: backup`) are fetched alongside them.
   The run log and job summary list any feed that failed.
2. **Normalize:** clean titles, enforce trusted-source lists, and collapse near-duplicate coverage.
3. **Recover:** aim for at least twelve candidates per relevant country. If the Google News
   searches come back thin (or fail), top up from the direct publisher feeds marked
   `tier: backup`. Only if that is still short, widen the lookback to 7 days (never further, so
   old stories can't pose as today's news).
4. **Edit:** send the candidate metadata to OpenAI using strict structured output. The model chooses
   the lead, secondary stories, speed reads, setup, subject, and preview text.
5. **Validate:** snap every generated URL back to a real candidate, reject invented sources, merge
   duplicate topics, remove recap bullets, and backfill speed reads from unused candidate topics
   when needed.
6. **Enrich:** fetch publisher social images concurrently under a fixed time budget, with RSS images
   as the fallback.
7. **Render:** build one responsive email template and the edition-specific plain-text fallback.
8. **Send:** deliver through Resend, scheduled for 06:00 when the run finishes early. Transient
   Resend errors are retried with the same idempotency key. That key is stable per edition and
   day, so if an earlier run already sent today's edition Resend refuses the copy and the run
   records it as delivered. Forced revisions get a unique workflow-run nonce so they can still be
   sent intentionally.

## Code map

- `src/briefing/collect.py` — feed collection, deduplication, Google News URL resolution, images
- `src/briefing/editor.py` — OpenAI request orchestration and date/localization prompt setup
- `src/briefing/editorial.py` — schemas, newsroom style guide, source validation, quality gates
- `src/briefing/models.py` — current newsletter data model and structured-output parsing
- `src/briefing/render.py` — cached Jinja template rendering and plain-text output
- `src/briefing/send.py` — recipient parsing, Resend delivery, retries, and duplicate handling
- `src/briefing/delivery.py` — delivery window, delivery records, and failure alerts (stdlib only;
  the workflows run it before installing dependencies)
- `src/briefing/templates/newsletter.html` — email-safe responsive presentation
- `src/briefing/feedcheck.py` — checks every configured feed and the resulting candidate pool
- `config/sources.yml` — standard Sweden + Indonesia source discovery
- `config/indonesia_sources.yml` — Nusantara Daily source discovery
- `uv.lock` — exact dependency versions (with hashes) used in production and CI
- `.github/workflows/feed-check.yml` — runs the feed check when sources change, or on demand
- `.github/dependabot.yml` — weekly grouped dependency update PRs
- `.github/workflows/briefs.yml` — primary trigger (external 05:45 dispatch, manual runs)
- `.github/workflows/briefs-backstop.yml` — hourly GitHub cron safety net
- `.github/workflows/send-newsletter.yml` — shared guard, generation, delivery record, and alert

## Maintenance

- **Dependency updates:** Dependabot opens one grouped PR per week for Python packages and one
  for GitHub Actions (minor and patch versions only). The Test workflow runs on each, including
  a contract test of the OpenAI SDK request/response shape; merge when green. Production keeps
  the locked versions until you merge. Major versions are ignored on purpose, since tests can't
  exercise the real OpenAI API or the delivery workflows; upgrade those deliberately with
  `uv lock --upgrade-package <name>` and a manual test send.
- **Feeds:** after editing `config/*.yml`, the Feed check workflow fetches every feed from
  GitHub's network. Dead feeds show as warnings; a country left with fewer than 6 usable stories
  fails the check. You can also run it from the Actions tab at any time.
- **Past editions:** each run that generated a brief keeps the HTML and text for 90 days under
  Actions → the run → Artifacts (`edition-<edition>-<date>`).

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

- The edition date comes from the workflow, not the AI editor, so duplicate protection can't be
  thrown off by a model writing the date differently.
- If the editor returns output that can't be parsed or validated, it is regenerated once.
- Feed failures are tolerated individually, and every run lists the failed feeds in its log and
  job summary.
- If Google News is thin, failing, or blocked, direct publisher feeds top each country up to 12
  candidates. Older coverage is only used as a last resort, and never beyond 7 days.
- Production installs exact versions from `uv.lock`, and delivery jobs run on a pinned
  runner image (`ubuntu-24.04`), so nothing changes underneath a morning run unexpectedly.
- The editor needs at least six candidate stories per relevant country after fallback so it can fill
  the minimum structured edition without recycling topics.
- Image failures never block delivery; the newsletter can send with RSS images or no image.
- Both editions and both triggers use the same reusable delivery workflow, preventing drift.
- The GitHub test workflow runs Ruff, Pytest, and a full sample render on code/config changes;
  marker-only delivery commits are ignored.
