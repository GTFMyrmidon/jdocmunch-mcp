# Authentication

The API uses bearer tokens. Tokens are short-lived and must be
refreshed before expiry.

## Obtaining a token

POST to `/auth/token` with your API key in the body. The response
includes `access_token` and `expires_in` (seconds).

```bash
curl -X POST https://api.example.com/auth/token \
  -H "Content-Type: application/json" \
  -d '{"api_key": "..."}'
```

## Refreshing tokens

When `expires_in` reaches zero, the next request returns 401. Repeat
the POST to `/auth/token` to obtain a fresh token.

## Token rotation

Long-running services should rotate tokens proactively at 80% of
`expires_in`. Storing the token in a refresh-aware client wrapper is
the recommended pattern.

## Common errors

- `401 Unauthorized` — token expired or revoked. Refresh and retry.
- `403 Forbidden` — token is valid but lacks scope. Check scopes on
  the API-key dashboard.
- `429 Too Many Requests` — rate-limit hit. Honor `Retry-After`.
