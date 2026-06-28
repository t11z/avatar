# avatar

A lightweight, self-hosted, trigger-based social media bot. **avatar** posts on a
schedule and replies to mentions, using an LLM to generate text in a persona you
define. Platforms, model providers and the optional content-safety hook are all
pluggable, and everything starts in **dry-run** mode so you can watch what it
*would* do before it does anything for real.

## What it is

avatar is a small, dependency-light Python service (Python 3.12+). You give it a
persona, point it at one or more social platforms and an LLM provider, and it:

- wakes up on cron schedules to publish original posts, and/or
- watches for mentions and replies in character.

Configuration is a single `config.yaml`; all secrets stay in environment
variables and are referenced with `${VAR}` interpolation, so they never live in
the config file or the logs.

## Features

- **Time triggers** — cron-based schedules (`croniter` syntax) with optional
  `jitter_seconds` so posts are not perfectly predictable.
- **Mention triggers** — poll mentions per platform with `allow`/`deny` lists,
  `allow_patterns`/`deny_patterns` (regex), per-user cooldowns, and
  `ignore_self`/`ignore_bots` filtering.
- **Platforms** — Bluesky, X (Twitter) and Threads. Adapters are registered
  plugins; enable the ones you have credentials for.
- **Model providers** — Anthropic (Claude), OpenAI, Google (Gemini) and Ollama,
  with **rolling model discovery**: models are listed live from each provider, so
  you can name any model the provider currently offers without a code change.
- **Optional security hook** — a vendor-neutral content scanner that maps your
  text into any HTTP scan service's request shape and extracts a verdict via
  JMESPath. Configurable fail mode (open/closed) and on-block behavior.
- **Observability** — Prometheus metrics, health/readiness endpoints, structured
  (JSON) logging via `structlog`, and optional OpenTelemetry/OTLP tracing.
- **Dry-run** — `dry_run: true` (the default) generates and logs content without
  publishing anything.

## Quickstart

```bash
# 1. Clone
git clone https://github.com/t11z/avatar.git
cd avatar

# 2. Create your config and secrets from the examples
cp config.example.yaml config.yaml
cp .env.example .env
# edit config.yaml (persona, platforms, schedules, mentions)
# put your secrets in .env (API keys, app passwords, ...)
```

Run it with Docker Compose:

```bash
cp docker-compose.example.yaml docker-compose.yaml
docker compose up
```

…or run it directly with the CLI (install the package plus the extras you need —
see [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup):

```bash
pip install -e ".[all]"
avatar run --config config.yaml
```

avatar ships with `dry_run: true`, so the first run will only *log* the posts and
replies it would make. Flip it to `false` once you are happy.

## Configuration

Everything lives in `config.yaml`. The fully-commented reference is
[`config.example.yaml`](config.example.yaml); the top-level sections are:

| Section         | Purpose                                                                 |
| --------------- | ----------------------------------------------------------------------- |
| `dry_run`       | When `true`, generate and log content but never publish.                |
| `log`           | Log `level` and `format` (`json` or console).                           |
| `observability` | `metrics_addr`, `health_addr`, and `tracing` (OTel/OTLP) settings.      |
| `store`         | Persistence backend: `sqlite` (with `path`) or `memory`.                |
| `persona`       | The `system_prompt` and the `scheduled` / `reply` prompt `templates`.   |
| `model`         | Default `provider`, `model`, `max_tokens`, `reasoning`.                  |
| `providers`     | Per-provider settings (API keys, Ollama `host`).                        |
| `platforms`     | List of platform configs (`id`, `type`, `enabled`, credentials, …).     |
| `schedules`     | Cron schedules with `jitter_seconds`, target `platform`, `template`.    |
| `mentions`      | Mention polling: `platforms`, allow/deny lists, cooldown, ignore flags. |
| `limits`        | Rate limits such as `max_posts_per_day` and per-user cooldowns.         |
| `security`      | Optional content-scan hook (scanners, fail mode, on-block behavior).    |

Secrets are referenced as `${VAR}` (with optional `${VAR:-default}` defaults) and
resolved from the environment at load time. Copy `.env.example` to `.env` and fill
it in.

## CLI

The `avatar` command exposes:

| Command                       | Description                                                      |
| ----------------------------- | ---------------------------------------------------------------- |
| `avatar run [-c config.yaml]` | Start the bot (schedules + mention polling).                     |
| `avatar validate [-c …]`      | Load a config and print a summary; exits non-zero if invalid.   |
| `avatar models [-c …] [-p P]` | List models discovered live from the configured providers.      |
| `avatar version`              | Print the installed version.                                     |

`--config`/`-c` defaults to `config.yaml`. For `models`, `--provider`/`-p` limits
output to a single provider.

## Observability

avatar serves operational endpoints (addresses are configurable under
`observability` in the config):

- `GET /healthz` — liveness.
- `GET /readyz` — readiness.
- `GET /metrics` — Prometheus metrics.

By default health/readiness are on `:8080` and metrics on `:9090`. Tracing is
**OpenTelemetry over OTLP**: enable it under `observability.tracing` and point
`otlp_endpoint` at your collector (requires the `otel` extra).

## Platform notes

Not all platforms are equally open, so plan accordingly:

- **Bluesky** — free and open; recommended starting point. Authenticate with a
  handle and an app password. Both posting and mention polling work.
- **X (Twitter)** — requires a **paid API tier** to post and to read mentions.
  Disabled in the example config until you have access.
- **Threads** — uses the Meta Graph API. Posting is supported, but **reading
  mentions is limited** by what the API exposes.

## License & contributing

avatar is released under the [MIT License](LICENSE). Contributions are welcome —
see [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup and a guide to adding new
platform, model, or scanner adapters, and please review our
[Code of Conduct](CODE_OF_CONDUCT.md). To report a security issue, see
[SECURITY.md](SECURITY.md).
