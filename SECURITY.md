# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities.

Instead, report them privately through GitHub's
[private security advisories](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability):
go to the repository's **Security** tab and choose **Report a vulnerability**.
This opens a private advisory visible only to you and the maintainers.

When reporting, please include:

- a description of the issue and its potential impact,
- steps to reproduce (a minimal proof of concept if possible),
- affected version(s) or commit, and
- any suggested remediation.

We will acknowledge your report, investigate, and keep you updated on progress
toward a fix. Once a fix is available we will coordinate disclosure with you.

## Supported versions

avatar is pre-1.0 and under active development. Security fixes are applied to the
latest released version and `main`. Please upgrade to the latest version before
reporting.

## Secrets handling

avatar is designed so that secrets never end up in source control or logs:

- **Environment-only secrets.** All credentials (API keys, app passwords,
  tokens) are supplied via environment variables and referenced from
  `config.yaml` with `${VAR}` interpolation. They are not stored in the config
  file itself. Use `.env` (copied from `.env.example`) for local development and
  never commit it.
- **Never logged.** Secret values are resolved at config-load time and are not
  emitted in structured logs, metrics, or traces. If you believe a secret is
  being exposed anywhere in output, please treat it as a vulnerability and report
  it via the process above.

When deploying, prefer your platform's secret manager (Docker/Compose `env_file`,
Kubernetes secrets, your cloud provider's secret store) over baking secrets into
images or config.
