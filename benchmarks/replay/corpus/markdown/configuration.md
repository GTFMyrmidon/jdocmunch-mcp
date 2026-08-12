# Configuration

Configuration lives in `config.toml` at the project root, or in
environment variables prefixed with `EXAMPLE_`.

## File location

The loader searches in order:

1. `$EXAMPLE_CONFIG_PATH` if set
2. `./config.toml` in the working directory
3. `~/.config/example/config.toml`

The first file found wins; later locations are not merged.

## Required fields

- `api_key` — your API key. May be supplied via `EXAMPLE_API_KEY`
  instead of the file.
- `endpoint` — base URL of the API. Defaults to
  `https://api.example.com`.

## Optional fields

- `timeout_seconds` (default 30)
- `retry_count` (default 3)
- `log_level` (default `INFO`)

## Example

```toml
[default]
api_key = "..."
endpoint = "https://api.example.com"
timeout_seconds = 60
retry_count = 5
log_level = "DEBUG"
```

## Environment overrides

Any field can be overridden via `EXAMPLE_<UPPER_FIELD>`. The env var
wins over the file.
