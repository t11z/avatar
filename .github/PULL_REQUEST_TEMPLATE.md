<!-- Thanks for contributing to avatar! Please fill in the sections below. -->

## Summary

<!-- What does this PR do and why? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] New adapter (platform / model / scanner)
- [ ] Documentation
- [ ] Refactor / chore

## Related issues

<!-- e.g. "Closes #123" -->

## Checklist

- [ ] I read [CONTRIBUTING.md](../CONTRIBUTING.md).
- [ ] `ruff check .` passes.
- [ ] `mypy avatar` passes.
- [ ] `pytest -q` passes.
- [ ] New/changed adapters lazy-import their optional SDKs and tolerate missing secrets at construction.
- [ ] New/changed adapters have a test subclassing the matching contract in `tests/contract.py`, with the network layer mocked.
- [ ] Docs updated (`README.md` / `config.example.yaml`) for any user-facing config changes.
- [ ] No secrets are committed; secrets remain environment-only.

## Notes for reviewers

<!-- Anything the reviewer should know, e.g. things to verify manually. -->
