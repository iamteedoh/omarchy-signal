<!-- Thanks for contributing to omarchy-signal! -->

## What does this PR do?

<!-- Briefly describe the change and why it is needed. -->

## Related issue

<!-- Link the public issue this addresses, for example: Closes #123. -->

## Validation

<!-- List the commands and manual checks you ran (make test, and what you tried on a real Omarchy desktop). -->

## Checklist

- [ ] `make test` passes (python, node and bash suites; the QML load check on an Omarchy machine)
- [ ] `yamllint .` passes
- [ ] gitleaks reports no secrets in Git history
- [ ] Source files include a GPL-3.0-or-later SPDX header
- [ ] No secrets, tokens, phone numbers, message history, or private infrastructure details are committed
- [ ] Anything a remote sender controls still goes through `sanitize.py` / `Model.cleanText`
- [ ] Documentation is updated for user-visible or operational changes
- [ ] The PR title follows Conventional Commits
