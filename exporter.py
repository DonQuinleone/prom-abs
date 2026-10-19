#!/usr/bin/env python3
"""
Audiobookshelf -> Prometheus exporter.

An "exporter" is just a small web service that, when Prometheus scrapes its
/metrics endpoint, goes and asks another system (here, the Audiobookshelf API)
for some numbers and hands them back in Prometheus' text format.

We use a custom Collector so the ABS API is queried fresh on every scrape,
rather than on a background timer. Simple, and always current.

Two families of metrics:
  * Inventory / health  — libraries, item counts, sizes, users, who's online.
  * Listening activity  — sessions, seconds listened, per-user and per-weekday,
                          computed over a recent window of sessions.

Config comes from environment variables:
  ABS_URL             base URL of your Audiobookshelf server (default http://audiobookshelf:80)
  ABS_TOKEN           an Audiobookshelf API key / admin token (required)
  LISTEN_PORT         port this exporter serves on            (default 9835)
  ABS_TIMEOUT         per-request timeout in seconds          (default 10)
  ABS_SESSIONS_LIMIT  how many recent sessions to summarise   (default 1000)
"""
import os
import time
import logging

import requests
from prometheus_client import start_http_server, REGISTRY
from prometheus_client.core import GaugeMetricFamily

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("abs_exporter")

ABS_URL = os.environ.get("ABS_URL", "http://audiobookshelf:80").rstrip("/")
ABS_TOKEN = os.environ.get("ABS_TOKEN", "")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "9835"))
TIMEOUT = float(os.environ.get("ABS_TIMEOUT", "10"))
SESSIONS_LIMIT = int(os.environ.get("ABS_SESSIONS_LIMIT", "1000"))

HEADERS = {"Authorization": f"Bearer {ABS_TOKEN}"}


def _get(path):
    """GET a JSON endpoint from Audiobookshelf, raising on any HTTP error."""
    resp = requests.get(f"{ABS_URL}{path}", headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


class ABSCollector(object):
    """Queried by prometheus_client once per scrape."""

    def collect(self):
        started = time.time()

        up = GaugeMetricFamily("abs_up", "1 if the Audiobookshelf API is reachable and the token is valid, else 0")
        scrape_duration = GaugeMetricFamily("abs_scrape_duration_seconds", "Seconds spent scraping the Audiobookshelf API")

        # --- inventory / health ---
        libraries_total = GaugeMetricFamily("abs_libraries_total", "Number of libraries on the server")
        lib_labels = ["library", "media_type"]
        lib_items = GaugeMetricFamily("abs_library_items_total", "Items in a library", labels=lib_labels)
        lib_duration = GaugeMetricFamily("abs_library_duration_seconds", "Total duration of a library's items", labels=lib_labels)
        lib_size = GaugeMetricFamily("abs_library_size_bytes", "Total on-disk size of a library", labels=lib_labels)
        lib_authors = GaugeMetricFamily("abs_library_authors_total", "Distinct authors in a library", labels=lib_labels)
        lib_genres = GaugeMetricFamily("abs_library_genres_total", "Distinct genres in a library", labels=lib_labels)
        lib_tracks = GaugeMetricFamily("abs_library_audiotracks_total", "Audio tracks in a library", labels=lib_labels)
        users_total = GaugeMetricFamily("abs_users_total", "Number of user accounts")
        users_online = GaugeMetricFamily("abs_users_online_total", "Users currently online")
        active_sessions = GaugeMetricFamily("abs_active_sessions_total", "Open playback sessions right now")

        # --- listening activity (over a recent window of sessions) ---
        sessions_total = GaugeMetricFamily("abs_sessions_total", "All-time number of playback sessions recorded")
        recent_sessions = GaugeMetricFamily("abs_recent_sessions", "Playback sessions summarised this scrape (see ABS_SESSIONS_LIMIT)")
        recent_listen = GaugeMetricFamily("abs_recent_listening_seconds", "Total seconds listened across the recent window")
        recent_listen_user = GaugeMetricFamily("abs_recent_listening_seconds_by_user", "Seconds listened per user in the recent window", labels=["user"])
        recent_listen_weekday = GaugeMetricFamily("abs_recent_listening_seconds_by_weekday", "Seconds listened per weekday in the recent window", labels=["weekday"])

        healthy = True
        user_names = {}

        # Libraries + per-library stats. Also doubles as our auth check.
        try:
            libraries = _get("/api/libraries").get("libraries", [])
            libraries_total.add_metric([], len(libraries))
            for lib in libraries:
                name = lib.get("name", "unknown")
                media_type = lib.get("mediaType", "unknown")
                lib_id = lib.get("id")
                try:
                    stats = _get(f"/api/libraries/{lib_id}/stats")
                    labels = [name, media_type]
                    lib_items.add_metric(labels, stats.get("totalItems", 0))
                    lib_duration.add_metric(labels, stats.get("totalDuration", 0))
                    lib_size.add_metric(labels, stats.get("totalSize", 0))
                    lib_authors.add_metric(labels, stats.get("totalAuthors", 0))
                    lib_genres.add_metric(labels, stats.get("totalGenres", 0))
                    lib_tracks.add_metric(labels, stats.get("numAudioTrack", 0))
                except Exception as exc:
                    log.warning("stats fetch failed for library '%s': %s", name, exc)
        except Exception as exc:
            healthy = False
            log.error("failed to fetch /api/libraries (check ABS_URL and ABS_TOKEN): %s", exc)

        # Users (requires an admin token). Build id -> name map for sessions.
        try:
            users = _get("/api/users").get("users", [])
            users_total.add_metric([], len(users))
            for u in users:
                if u.get("id"):
                    user_names[u["id"]] = u.get("username", u["id"])
        except Exception as exc:
            log.warning("failed to fetch /api/users: %s", exc)

        # Currently online users / open playback sessions (shape varies by version).
        try:
            online = _get("/api/users/online")
            if isinstance(online, dict):
                users_list = online.get("usersOnline", online.get("users", []))
                sessions_list = online.get("openSessions", online.get("sessions", []))
                users_online.add_metric([], len(users_list) if isinstance(users_list, list) else 0)
                active_sessions.add_metric([], len(sessions_list) if isinstance(sessions_list, list) else 0)
            elif isinstance(online, list):
                users_online.add_metric([], len(online))
        except Exception as exc:
            log.warning("failed to fetch /api/users/online: %s", exc)

        # Listening activity from a recent window of sessions.
        try:
            data = _get(f"/api/sessions?itemsPerPage={SESSIONS_LIMIT}&page=0")
            sessions = data.get("sessions", []) if isinstance(data, dict) else []
            sessions_total.add_metric([], data.get("total", len(sessions)) if isinstance(data, dict) else len(sessions))
            recent_sessions.add_metric([], len(sessions))

            per_user, per_weekday, grand = {}, {}, 0.0
            for s in sessions:
                secs = s.get("timeListening") or 0
                grand += secs
                uid = s.get("userId") or "unknown"
                uname = user_names.get(uid, uid)
                per_user[uname] = per_user.get(uname, 0.0) + secs
                weekday = s.get("dayOfWeek") or "unknown"
                per_weekday[weekday] = per_weekday.get(weekday, 0.0) + secs

            recent_listen.add_metric([], grand)
            for uname, secs in per_user.items():
                recent_listen_user.add_metric([uname], secs)
            for weekday, secs in per_weekday.items():
                recent_listen_weekday.add_metric([weekday], secs)
        except Exception as exc:
            log.warning("failed to fetch /api/sessions: %s", exc)

        up.add_metric([], 1 if healthy else 0)
        scrape_duration.add_metric([], time.time() - started)

        for metric in (
            up, scrape_duration, libraries_total,
            lib_items, lib_duration, lib_size, lib_authors, lib_genres, lib_tracks,
            users_total, users_online, active_sessions,
            sessions_total, recent_sessions, recent_listen,
            recent_listen_user, recent_listen_weekday,
        ):
            yield metric


if __name__ == "__main__":
    if not ABS_TOKEN:
        log.warning("ABS_TOKEN is empty - every API call will 401 until you set it in .env")
    REGISTRY.register(ABSCollector())
    log.info("Audiobookshelf exporter listening on :%d, target %s", LISTEN_PORT, ABS_URL)
    start_http_server(LISTEN_PORT)
    while True:
        time.sleep(3600)
