# Changelog

## [0.4.3](https://github.com/iamteedoh/omarchy-signal/compare/v0.4.2...v0.4.3) (2026-09-18)


### Documentation

* say what the logging guarantee actually covers ([#21](https://github.com/iamteedoh/omarchy-signal/issues/21)) ([e63d664](https://github.com/iamteedoh/omarchy-signal/commit/e63d664d66e485a47a4e3350052b4008fbf4b452))
* warn that the release override marker must appear once in a PR body ([#19](https://github.com/iamteedoh/omarchy-signal/issues/19)) ([2807d92](https://github.com/iamteedoh/omarchy-signal/commit/2807d9224c8ec0456e844648f264fd84dc02a808))

## [0.4.2](https://github.com/iamteedoh/omarchy-signal/compare/v0.4.1...v0.4.2) (2026-09-18)


### Bug Fixes

* keep the installer from corrupting the Omarchy menu file ([5e79615](https://github.com/iamteedoh/omarchy-signal/commit/5e79615e4afcf34405805d68a1792bda18d51de5))
* keep the installer from truncating bindings.lua when a marker is missing ([5e79615](https://github.com/iamteedoh/omarchy-signal/commit/5e79615e4afcf34405805d68a1792bda18d51de5))
* report the installed release from --version and status ([#16](https://github.com/iamteedoh/omarchy-signal/issues/16)) ([3551063](https://github.com/iamteedoh/omarchy-signal/commit/3551063c53678de2ec59c0237921c7b1bc65b77c))
* stop the install-time QML check failing at random ([5e79615](https://github.com/iamteedoh/omarchy-signal/commit/5e79615e4afcf34405805d68a1792bda18d51de5))
* wrap the chat window's bottom legend instead of cutting it off ([5e79615](https://github.com/iamteedoh/omarchy-signal/commit/5e79615e4afcf34405805d68a1792bda18d51de5))


### Tests

* run the installer end to end, and verify its copy list by running it ([5e79615](https://github.com/iamteedoh/omarchy-signal/commit/5e79615e4afcf34405805d68a1792bda18d51de5))


### Build & Packaging

* put every kind of change in the release notes ([5e79615](https://github.com/iamteedoh/omarchy-signal/commit/5e79615e4afcf34405805d68a1792bda18d51de5))

## [0.4.1](https://github.com/iamteedoh/omarchy-signal/compare/v0.4.0...v0.4.1) (2026-09-17)


### Bug Fixes

* work on Omarchy 4.0.0, and catch version-skewed APIs in CI ([#14](https://github.com/iamteedoh/omarchy-signal/issues/14)) ([b427b66](https://github.com/iamteedoh/omarchy-signal/commit/b427b6676ea6dd73b8179f1440d165da10e77085))

## [0.4.0](https://github.com/iamteedoh/omarchy-signal/compare/v0.3.0...v0.4.0) (2026-09-16)


### Features

* installer shows progress for every step ([#11](https://github.com/iamteedoh/omarchy-signal/issues/11)) ([66270f1](https://github.com/iamteedoh/omarchy-signal/commit/66270f14b9c2cb2d8c70564121412e8c84459d3b))
* multi-line message box in the popup and chat window ([#12](https://github.com/iamteedoh/omarchy-signal/issues/12)) ([650f7ab](https://github.com/iamteedoh/omarchy-signal/commit/650f7ab3e87f2911535f93e25fe2497ca7706334))

## [0.3.0](https://github.com/iamteedoh/omarchy-signal/compare/v0.2.0...v0.3.0) (2026-09-16)


### Features

* copy and paste with mouse and keyboard ([#8](https://github.com/iamteedoh/omarchy-signal/issues/8)) ([7f200b0](https://github.com/iamteedoh/omarchy-signal/commit/7f200b0bbb74010f48d17ed10c242ba4ff84c057))
* SUPER+W closes the popup conversation and the QR code ([#9](https://github.com/iamteedoh/omarchy-signal/issues/9)) ([660be16](https://github.com/iamteedoh/omarchy-signal/commit/660be162f34a72cd2209ac6e34e0c74d1fcb1b29))

## [0.2.0](https://github.com/iamteedoh/omarchy-signal/compare/v0.1.0...v0.2.0) (2026-09-09)


### Features

* guide setup after omarchy plugin add, full-length README animation ([8213fdf](https://github.com/iamteedoh/omarchy-signal/commit/8213fdf48ea6220cb6001b077c2508093f5e2253))

## 0.1.0 (2026-09-08)

Initial public release. Adopts the Teedoh Labs OSS repo standard: CI
(tests + gitleaks secret scan + community & licensing checks), release
automation, GPLv3 licensing, and community health files.

All notable changes to omarchy-signal are documented here.

This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Release entries are maintained by release-please from Conventional Commit PR
titles merged into `main`.

Releases are tagged `vX.Y.Z`; the release PR also bumps `version` in
`manifest.json`, which is what the plugin marketplace displays.
