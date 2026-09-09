# Changelog

Notable changes per release. Versions follow [SemVer](https://semver.org).

## 0.1.1 - 2026-09-09

### Fixed

- A poll interrupted by a link drop now fails immediately with "the link
  dropped" instead of waiting out `REPLY_TIMEOUT` and reporting the CCCD
  subscription diagnostic, which pointed at the wrong cause.
