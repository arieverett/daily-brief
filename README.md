# Daily Brief

Two personal morning newsletters: an English Sweden + Indonesia edition and a Bahasa Indonesia
edition for Ari's mom. They collect recent reporting, deduplicate overlapping headlines, ask an AI
editor to prioritize the news, render polished HTML and plain-text editions, and send via Resend.

## What ships in V1

- 30-second **Front Page** with the three stories that define the day
- Sweden and Indonesia sections with a lead, short explainers, and speed reads
- Editorial preference for significance, authoritative sourcing, and economic/political context
- Exact source links on every item
- Automated 5:45 AM Phoenix run, targeting inbox delivery by 6:00 AM
- Duplicate-send protection through a per-day Resend idempotency key
- HTML email plus plain-text fallback
- A no-API sample renderer and unit tests

## One-time launch setup

The scheduled workflow needs four repository secrets:

| Secret | Value |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key with billing enabled |
| `RESEND_API_KEY` | Resend sending API key |
| `BRIEF_TO_EMAIL` | Ari's destination email address |
| `INDONESIA_BRIEF_TO_EMAIL` | Recipient for Nusantara Daily |
| `BRIEF_FROM_EMAIL` | Verified sender, e.g. `Daily Brief <brief@yourdomain.com>` |

For the quickest first send, Resend's test sender can deliver only to the email attached to the
Resend account. A verified domain is the durable production setup.

After adding the secrets, manually run both **Send daily brief** and **Send Indonesia brief** once.
Both editions are scheduled for 3:00 AM America/New_York and account for daylight saving time.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Export the variables from `.env`, then:

```bash
# Render the bundled design sample (no API keys or network needed)
python -m briefing --sample

# Generate a live edition without sending
python -m briefing

# Generate and send
python -m briefing --send

# Generate and send Nusantara Daily
python -m briefing --edition indonesia --sources config/indonesia_sources.yml --send
```

Generated HTML and text files are written to `out/`.

## Editorial pipeline

1. Pull the last 36 hours from country, city, economy, and authoritative RSS searches.
2. Normalize titles and collapse near-duplicate coverage.
3. Require at least six fresh candidates for each country or stop without sending.
4. Use structured AI output with strict story counts and fields.
5. Reject any link/source pair the model did not receive in the candidate set.
6. Render responsive email HTML and a plain-text fallback.
7. Send once per date and recipient through Resend.

## V1 operating notes

- Sources are editable in `config/sources.yml` without touching the application code.
- Nusantara Daily sources are isolated in `config/indonesia_sources.yml`.
- The model is configurable with `OPENAI_MODEL`; the default is `gpt-5-mini`.
- Feed failure is tolerated, but the minimum-story safety check prevents thin editions.
- The workflow can be run manually at any time from GitHub Actions.
- This is a personal briefing, not a bulk marketing list, so subscriber management and
  unsubscribe workflows are intentionally outside V1.
