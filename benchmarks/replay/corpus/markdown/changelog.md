# Changelog

## 1.4.0

- New `--retry-count` CLI flag.
- Connection pooling now respects `timeout_seconds`.

## 1.3.0

- Added support for OpenAPI 3.1 specs.
- Bug fix: token refresh no longer races on concurrent requests.

## 1.2.0

- Authentication now uses short-lived bearer tokens.
- Removed `--legacy-auth` flag (deprecated since 1.0).

## 1.1.0

- Initial public release.
