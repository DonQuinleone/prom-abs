# abs-prom-exporter

[Source](https://github.com/DonQuinleone/prom-abs)

A small Prometheus exporter for [Audiobookshelf](https://www.audiobookshelf.org/).
Audiobookshelf has no native `/metrics` endpoint, so this service queries its
HTTP API on each scrape and translates the results into Prometheus metrics.

It exposes two families of data: **inventory/health** (library sizes, item
counts, users, who is online) and **listening activity** (sessions, seconds
listened, broken down by user and by weekday).

## Metrics

| Metric | Labels | Meaning |
| --- | --- | --- |
| `abs_up` | | 1 if the API is reachable and the token is valid |
| `abs_scrape_duration_seconds` | | Time spent scraping the API |
| `abs_libraries_total` | | Number of libraries |
| `abs_library_items_total` | `library`, `media_type` | Items in a library |
| `abs_library_duration_seconds` | `library`, `media_type` | Total duration of a library |
| `abs_library_size_bytes` | `library`, `media_type` | On-disk size of a library |
| `abs_library_authors_total` | `library`, `media_type` | Distinct authors |
| `abs_library_genres_total` | `library`, `media_type` | Distinct genres |
| `abs_library_audiotracks_total` | `library`, `media_type` | Audio tracks |
| `abs_users_total` | | User accounts |
| `abs_users_online_total` | | Users currently online |
| `abs_active_sessions_total` | | Open playback sessions right now |
| `abs_sessions_total` | | All-time playback sessions recorded |
| `abs_recent_sessions` | | Sessions summarised this scrape |
| `abs_recent_listening_seconds` | | Seconds listened across the recent window |
| `abs_recent_listening_seconds_by_user` | `user` | Seconds listened per user (recent window) |
| `abs_recent_listening_seconds_by_weekday` | `weekday` | Seconds listened per weekday (recent window) |

The `recent_*` metrics are computed over the most recent `ABS_SESSIONS_LIMIT`
sessions (default 1000), not all-time, so they stay cheap to collect.

## Configuration

Set via environment variables (see `.env.example`):

| Variable | Default | Notes |
| --- | --- | --- |
| `ABS_URL` | `http://audiobookshelf:80` | Base URL of your Audiobookshelf server |
| `ABS_TOKEN` | *(required)* | An Audiobookshelf API key or admin token |
| `LISTEN_PORT` | `9835` | Port the exporter serves `/metrics` on |
| `ABS_TIMEOUT` | `10` | Per-request timeout, seconds |
| `ABS_SESSIONS_LIMIT` | `1000` | How many recent sessions to summarise |

The token must belong to an **admin** user so that `/api/users` and
`/api/users/online` are accessible.

## Running

```sh
cp .env.example .env    # then paste your ABS token into .env
docker compose up -d --build
```

A prebuilt multi-platform image (`linux/amd64`, `linux/arm64`) is published
for every push to `master` and every `v*` tag:

```sh
docker pull ghcr.io/donquinleone/prom-abs:latest
```

Each build is scanned with Trivy and only published if it is clean at every
severity.

Nothing is published on the host; Prometheus reaches it by container name on
the shared Docker network as `abs-exporter:9835`.

Prometheus scrape job:

```yaml
  - job_name: audiobookshelf
    scrape_interval: 60s
    static_configs:
      - targets: ["abs-exporter:9835"]
```
