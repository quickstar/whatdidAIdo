#!/usr/bin/env python3
"""
ActivityWatch Worklog Analyzer
Queries the local ActivityWatch REST API and aggregates every synced device.

Usage:
  python worklog.py                    Interactive mode
  python worklog.py 27.01.2026         Analyze specific date
  python worklog.py 27.01.2026 --ai    Compact output for AI interpretation
"""

import sqlite3
import json
import sys
import re
import argparse
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone as datetime_timezone, tzinfo
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
from pathlib import Path, PureWindowsPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Configure UTF-8 output for Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Global config
CONFIG = {}

CODEX_APP_NAMES = ['ChatGPT.exe', 'Codex.exe']


class ActivityWatchAPIError(RuntimeError):
    """Raised when the local ActivityWatch read API cannot be used safely."""


class EuropeZurichTimezone(tzinfo):
    """Dependency-free Europe/Zurich fallback for Windows without IANA tzdata."""

    standard_offset = timedelta(hours=1)

    @staticmethod
    def _last_sunday(year, month):
        if month == 12:
            next_month = datetime(year + 1, 1, 1)
        else:
            next_month = datetime(year, month + 1, 1)
        last_day = next_month - timedelta(days=1)
        return last_day - timedelta(days=(last_day.weekday() + 1) % 7)

    def dst(self, value):
        if value is None:
            return timedelta(0)
        naive = value.replace(tzinfo=None)
        start = self._last_sunday(naive.year, 3).replace(hour=2)
        end = self._last_sunday(naive.year, 10).replace(hour=3)
        return timedelta(hours=1) if start <= naive < end else timedelta(0)

    def utcoffset(self, value):
        return self.standard_offset + self.dst(value)

    def tzname(self, value):
        return 'CEST' if self.dst(value) else 'CET'

    def __str__(self):
        return 'Europe/Zurich'


def resolve_timezone(timezone_name):
    """Resolve an IANA timezone, with a dependency-free Zurich fallback."""
    try:
        return ZoneInfo(str(timezone_name))
    except ZoneInfoNotFoundError as exc:
        if str(timezone_name) == 'Europe/Zurich':
            return EuropeZurichTimezone()
        raise ActivityWatchAPIError(
            f"Unknown ActivityWatch timezone {timezone_name!r}; install system tzdata "
            "or use Europe/Zurich"
        ) from exc


class ActivityWatchRESTClient:
    """Small read-only adapter for the aw-server-rust REST API."""

    def __init__(self, host='127.0.0.1', port=5600, timeout=30):
        self.host = str(host)
        self.port = int(port)
        self.timeout = float(timeout)
        self.base_url = f"http://{self.host}:{self.port}/api/0"

    def _get_json(self, path, params=None):
        query = f"?{urlencode(params)}" if params else ''
        request = Request(
            f"{self.base_url}/{path.lstrip('/')}{query}",
            headers={'Accept': 'application/json'},
            method='GET',
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            raise ActivityWatchAPIError(
                f"ActivityWatch API returned HTTP {exc.code} for {request.full_url}"
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise ActivityWatchAPIError(
                f"Cannot reach ActivityWatch at http://{self.host}:{self.port}; "
                "start aw-server-rust and verify the configured host and port"
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ActivityWatchAPIError(
                f"ActivityWatch returned invalid JSON for {request.full_url}"
            ) from exc

    def get_info(self):
        value = self._get_json('info')
        if not isinstance(value, dict):
            raise ActivityWatchAPIError('ActivityWatch /info response was not an object')
        return value

    def get_buckets(self):
        value = self._get_json('buckets/')
        if not isinstance(value, dict):
            raise ActivityWatchAPIError('ActivityWatch bucket response was not an object')
        buckets = []
        for bucket_id, metadata in value.items():
            if not isinstance(metadata, dict):
                continue
            buckets.append({'id': bucket_id, **metadata})
        return buckets

    def get_events(self, bucket_id, start, end):
        value = self._get_json(
            f"buckets/{quote(str(bucket_id), safe='')}/events",
            {'start': start.isoformat(), 'end': end.isoformat(), 'limit': -1},
        )
        if not isinstance(value, list):
            raise ActivityWatchAPIError(
                f"ActivityWatch events response for {bucket_id} was not a list"
            )
        return value


def get_activitywatch_settings(explicit_host=None, explicit_port=None):
    """Resolve ActivityWatch connection settings from CLI, env, config, defaults."""
    settings = CONFIG.get('activitywatch', {})
    host = explicit_host or os.environ.get('AW_HOST') or settings.get('host') or '127.0.0.1'
    port = explicit_port or os.environ.get('AW_PORT') or settings.get('port') or 5600
    timezone_name = settings.get('timezone') or CONFIG.get('github', {}).get('timezone') or 'Europe/Zurich'
    timeout = settings.get('timeout_seconds', 30)
    timezone = resolve_timezone(timezone_name)
    try:
        return str(host), int(port), timezone, float(timeout)
    except (TypeError, ValueError) as exc:
        raise ActivityWatchAPIError('ActivityWatch port and timeout must be numeric') from exc


def get_codex_home(explicit_path=None):
    """Get the Codex data directory from CLI, environment, config, or default."""
    value = explicit_path or os.environ.get('CODEX_HOME') or CONFIG.get('codex_home')
    if value:
        return Path(os.path.expandvars(os.path.expanduser(str(value))))
    return Path.home() / '.codex'


def load_config(config_path):
    """Load configuration from config.json if available."""
    global CONFIG
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            CONFIG = json.load(f)
    return CONFIG


def clean(s):
    """Remove non-ASCII characters for clean output."""
    return ''.join(c if ord(c) < 128 else '?' for c in str(s))


def normalize_for_match(s):
    """Normalize string for matching - remove accents and special chars."""
    import unicodedata
    # First normalize unicode
    result = str(s)
    # Replace common umlauts explicitly
    replacements = {
        'ö': 'o', 'ä': 'a', 'ü': 'u', 'ß': 'ss',
        'Ö': 'o', 'Ä': 'a', 'Ü': 'u',
        'é': 'e', 'è': 'e', 'ê': 'e', 'ë': 'e',
        'á': 'a', 'à': 'a', 'â': 'a',
        'í': 'i', 'ì': 'i', 'î': 'i',
        'ó': 'o', 'ò': 'o', 'ô': 'o',
        'ú': 'u', 'ù': 'u', 'û': 'u',
    }
    for old, new in replacements.items():
        result = result.replace(old, new)
    # Remove any remaining non-ASCII and special chars, keep alphanumeric and space
    result = ''.join(c if c.isalnum() or c.isspace() else '' for c in result)
    return result.lower()


def format_duration(seconds):
    """Format seconds as hours and minutes."""
    hours = seconds / 3600
    minutes = seconds / 60
    if hours >= 1:
        return f"{hours:.1f}h"
    return f"{minutes:.0f}m"


def parse_date(date_str):
    """Parse date string in various formats."""
    formats = [
        '%Y-%m-%d',      # 2026-01-27
        '%d.%m.%Y',      # 27.01.2026
        '%d/%m/%Y',      # 27/01/2026
        '%d-%m-%Y',      # 27-01-2026
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt
        except ValueError:
            continue
    return None


def day_datetime_bounds(target_date, timezone=None):
    """Return timezone-aware local-day boundaries, including DST transitions."""
    if timezone is None:
        _, _, timezone, _ = get_activitywatch_settings()
    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone)
    next_day = target_date.date() + timedelta(days=1)
    end = datetime(next_day.year, next_day.month, next_day.day, tzinfo=timezone)
    return start, end


def day_epoch_bounds(target_date, timezone=None):
    """Return configured-local-day boundaries as Unix timestamps."""
    start, end = day_datetime_bounds(target_date, timezone)
    return start.timestamp(), end.timestamp()


def parse_codex_timestamp(value):
    """Parse Codex epoch or RFC 3339 timestamps into Unix seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        return timestamp / 1000 if timestamp > 10_000_000_000 else timestamp

    text = str(value).strip()
    if not text:
        return None
    try:
        return parse_codex_timestamp(float(text))
    except ValueError:
        pass

    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        return parsed.timestamp()
    except ValueError:
        return None


def merge_intervals(intervals):
    """Merge overlapping timestamp intervals."""
    normalized = sorted((float(start), float(end)) for start, end in intervals if end > start)
    merged = []
    for start, end in normalized:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def interval_total_seconds(intervals):
    """Return the duration of a merged interval union."""
    return sum(end - start for start, end in merge_intervals(intervals))


def interval_overlap_seconds(left_intervals, right_intervals):
    """Calculate overlap between two collections of timestamp intervals."""
    left = merge_intervals(left_intervals)
    right = merge_intervals(right_intervals)
    overlap = 0.0
    left_index = 0
    right_index = 0

    while left_index < len(left) and right_index < len(right):
        left_start, left_end = left[left_index]
        right_start, right_end = right[right_index]
        overlap += max(0, min(left_end, right_end) - max(left_start, right_start))
        if left_end <= right_end:
            left_index += 1
        else:
            right_index += 1
    return overlap


CONCRETE_CODEX_OUTCOME_WORDS = (
    'completed', 'implemented', 'fixed', 'committed', 'pushed', 'merged',
    'verified', 'validated', 'built', 'build succeeded', 'tests passed',
    'released', 'updated', 'created', 'diagnosed', 'analyzed', 'reviewed',
    'refactored',
    'cleanup completed',
)


def codex_task_supports_evidence_union(task):
    """Conservatively select completed root tasks with concrete outcome evidence."""
    if task.get('status') != 'completed' or len(task.get('tickets') or []) > 1:
        return False
    if not (task.get('tickets') or task.get('branch') or task.get('cwd')):
        return False
    outcome = str(task.get('outcome') or '').lower()
    return bool(outcome) and any(word in outcome for word in CONCRETE_CODEX_OUTCOME_WORDS)


def calculate_evidence_union(results):
    """Build a conservative merged ActivityWatch + Codex interval candidate."""
    active = [
        (start.timestamp(), start.timestamp() + duration)
        for start, duration in results.get('active_intervals', [])
        if duration > 0
    ]
    accepted = list(active)
    task_ids = []
    for task in results.get('codex_tasks', []):
        if not codex_task_supports_evidence_union(task):
            continue
        accepted.extend(task.get('spans') or [])
        task_ids.append(task.get('thread_id'))
    merged = merge_intervals(accepted)
    return {
        'evidence_union_intervals': merged,
        'evidence_union_seconds': interval_total_seconds(merged),
        'evidence_union_codex_task_ids': [task_id for task_id in task_ids if task_id],
    }


def compact_codex_text(value, limit=160):
    """Turn a Codex task title or outcome into a safe one-line summary."""
    if not value:
        return ''
    text = str(value)
    text = re.sub(r'<oai-mem-citation>.*', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\[([^]]+)]\([^)]+\)', r'\1', text)
    text = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)
    text = re.sub(r'[`*_#>]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip().replace('|', '-')
    if len(text) > limit:
        return text[:limit - 1].rstrip() + '…'
    return text


def ticket_regex():
    """Build a ticket matcher from configured prefixes with Rooms defaults."""
    prefixes = list(CONFIG.get('ticket_prefixes', {}).keys()) or ['ITEM', 'ROMSD']
    escaped = '|'.join(re.escape(prefix) for prefix in sorted(prefixes, key=len, reverse=True))
    return re.compile(rf'\b(?:{escaped})-\d+\b', re.IGNORECASE)


def extract_tickets(*values):
    """Extract unique configured ticket IDs from arbitrary task context."""
    pattern = ticket_regex()
    tickets = set()
    for value in values:
        if value:
            tickets.update(match.upper() for match in pattern.findall(str(value)))
    return sorted(tickets)


def normalize_codex_path(value):
    """Normalize Windows extended paths stored by Codex."""
    text = str(value or '')
    if text.startswith('\\\\?\\'):
        return text[4:]
    return text


def codex_workspace_name(cwd):
    """Return a concise configured project or directory name for a Codex task."""
    normalized = normalize_codex_path(cwd)
    lowered = normalized.lower()
    for project_key, project_name in CONFIG.get('projects', {}).items():
        if str(project_key).lower() in lowered:
            return str(project_name)
    if re.match(r'^[A-Za-z]:[\\/]', normalized) or '\\' in normalized:
        return PureWindowsPath(normalized).name or normalized
    return Path(normalized).name or normalized or '-'


SUPPORTED_BUCKET_TYPES = {
    'afkstatus': 'afk',
    'currentwindow': 'window',
    'web.tab.current': 'web',
    'app.editor.activity': 'editor',
}


def logical_bucket_id(bucket_id):
    """Strip aw-sync provenance suffixes while retaining watcher identity."""
    return str(bucket_id).split('-synced-from-', 1)[0]


def raw_activitywatch_source(bucket):
    """Resolve the source label exposed by ActivityWatch sync metadata."""
    data = bucket.get('data') if isinstance(bucket.get('data'), dict) else {}
    origin = data.get('$aw.sync.origin')
    if origin:
        return str(origin)
    hostname = bucket.get('hostname')
    if hostname and str(hostname).lower() != 'unknown':
        return str(hostname)
    match = re.search(r'-synced-from-(.+)$', str(bucket.get('id', '')))
    return match.group(1) if match else str(hostname or 'unknown')


def get_activitywatch_source_aliases():
    """Return validated case-insensitive ActivityWatch source aliases."""
    settings = CONFIG.get('activitywatch', {})
    raw_aliases = settings.get('source_aliases', {}) if isinstance(settings, dict) else {}
    if raw_aliases is None:
        return {}
    if not isinstance(raw_aliases, dict):
        raise ActivityWatchAPIError(
            'activitywatch.source_aliases must be an object mapping aliases to canonical sources'
        )

    aliases = {}
    for raw_source, raw_canonical in raw_aliases.items():
        if not isinstance(raw_source, str) or not isinstance(raw_canonical, str):
            raise ActivityWatchAPIError(
                'ActivityWatch source aliases and canonical sources must be strings'
            )
        source = raw_source.strip()
        canonical = raw_canonical.strip()
        if not source or not canonical:
            raise ActivityWatchAPIError(
                'ActivityWatch source aliases and canonical sources must not be empty'
            )
        key = source.casefold()
        existing = aliases.get(key)
        if existing is not None and existing.casefold() != canonical.casefold():
            raise ActivityWatchAPIError(
                f'Conflicting ActivityWatch source aliases for {source!r}'
            )
        aliases[key] = canonical

    resolved = {}
    for alias, target in aliases.items():
        visited = {alias}
        while target.casefold() in aliases:
            next_alias = target.casefold()
            if next_alias in visited:
                raise ActivityWatchAPIError(
                    f'Cyclic ActivityWatch source alias involving {target!r}'
                )
            visited.add(next_alias)
            target = aliases[next_alias]
        resolved[alias] = target
    return resolved


def normalize_activitywatch_source(source, aliases=None):
    """Map a raw source label to its configured canonical device identity."""
    label = str(source or 'unknown')
    aliases = get_activitywatch_source_aliases() if aliases is None else aliases
    return aliases.get(label.casefold(), label)


def get_activitywatch_expected_sources(aliases=None):
    """Return the validated canonical source inventory, if configured."""
    settings = CONFIG.get('activitywatch', {})
    raw_sources = settings.get('expected_sources', []) if isinstance(settings, dict) else []
    if raw_sources is None:
        return []
    if not isinstance(raw_sources, list) or any(
        not isinstance(source, str) or not source.strip() for source in raw_sources
    ):
        raise ActivityWatchAPIError(
            'activitywatch.expected_sources must be a list of non-empty strings'
        )
    aliases = get_activitywatch_source_aliases() if aliases is None else aliases
    return sorted({
        normalize_activitywatch_source(source.strip(), aliases) for source in raw_sources
    })


def activitywatch_source(bucket, aliases=None):
    """Resolve and canonicalize a source label from ActivityWatch metadata."""
    return normalize_activitywatch_source(raw_activitywatch_source(bucket), aliases)


def parse_activitywatch_event(event, day_start, day_end):
    """Validate and clip one API event to the requested local day."""
    if not isinstance(event, dict) or not isinstance(event.get('data'), dict):
        return None
    try:
        timestamp = datetime.fromisoformat(str(event['timestamp']).replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=datetime_timezone.utc)
        duration = float(event.get('duration', 0))
    except (KeyError, TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    event_start = timestamp.timestamp()
    event_end = event_start + duration
    clipped_start = max(event_start, day_start.timestamp())
    clipped_end = min(event_end, day_end.timestamp())
    if clipped_end <= clipped_start:
        return None
    return clipped_start, clipped_end, event['data']


def activitywatch_event_fingerprint(source, kind, start, end, data):
    """Build a deterministic identity for exact copies imported more than once."""
    canonical_data = json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return source, kind, round(start, 6), round(end - start, 6), canonical_data


def empty_activitywatch_results():
    return {
        'app_time': defaultdict(float),
        'window_details': defaultdict(lambda: defaultdict(float)),
        'jira_tickets': defaultdict(float),
        'domain_time': defaultdict(float),
        'page_details': defaultdict(float),
        'file_time': defaultdict(float),
        'branches': defaultdict(float),
        'teams': defaultdict(float),
        'active_periods': [],
        'active_intervals': [],
        'total_active': 0,
        'codex_tasks': [],
        'activitywatch_sources': [],
        'activitywatch_source_aliases': {},
        'activitywatch_source_active': {},
        'activitywatch_bucket_counts': {},
        'activitywatch_source_app_time': defaultdict(lambda: defaultdict(float)),
        'activitywatch_source_window_details': defaultdict(
            lambda: defaultdict(lambda: defaultdict(float))
        ),
        'activitywatch_source_tickets': defaultdict(lambda: defaultdict(float)),
        'activitywatch_source_domain_time': defaultdict(lambda: defaultdict(float)),
        'activitywatch_source_file_time': defaultdict(lambda: defaultdict(float)),
        'activitywatch_source_branches': defaultdict(lambda: defaultdict(float)),
        'activitywatch_source_teams': defaultdict(lambda: defaultdict(float)),
        'activitywatch_cross_source_overlap': 0,
        'activitywatch_duplicate_events': 0,
        'activitywatch_malformed_events': 0,
        'activitywatch_warnings': [],
    }


def analyze_day(client, target_date, timezone=None):
    """Analyze one local day across every relevant API-visible device bucket."""
    if timezone is None:
        _, _, timezone, _ = get_activitywatch_settings()
    day_start, day_end = day_datetime_bounds(target_date, timezone)
    server_info = client.get_info()
    buckets = client.get_buckets()
    relevant_buckets = [bucket for bucket in buckets if bucket.get('type') in SUPPORTED_BUCKET_TYPES]
    source_aliases = get_activitywatch_source_aliases()
    expected_sources = get_activitywatch_expected_sources(source_aliases)
    used_source_aliases = {}

    results = empty_activitywatch_results()
    results['activitywatch_server'] = server_info
    results['activitywatch_timezone'] = str(timezone)
    for bucket in relevant_buckets:
        raw_source = raw_activitywatch_source(bucket)
        canonical_source = normalize_activitywatch_source(raw_source, source_aliases)
        if raw_source != canonical_source:
            used_source_aliases[raw_source] = canonical_source
    results['activitywatch_source_aliases'] = dict(
        sorted(used_source_aliases.items(), key=lambda item: item[0].casefold())
    )
    results['activitywatch_sources'] = sorted({
        activitywatch_source(bucket, source_aliases) for bucket in relevant_buckets
    })

    bucket_counts = defaultdict(lambda: defaultdict(int))
    bucket_types_by_source = defaultdict(set)
    seen_events = set()
    source_not_afk = defaultdict(list)
    raw_not_afk_intervals = []

    for bucket in relevant_buckets:
        source = activitywatch_source(bucket, source_aliases)
        kind = SUPPORTED_BUCKET_TYPES[bucket['type']]
        bucket_counts[source][kind] += 1
        bucket_types_by_source[source].add(kind)
        for raw_event in client.get_events(bucket['id'], day_start, day_end):
            if isinstance(raw_event, dict):
                try:
                    if float(raw_event.get('duration')) <= 0:
                        # Watchers expose their current heartbeat as a valid zero-duration event.
                        continue
                except (TypeError, ValueError):
                    pass
            parsed = parse_activitywatch_event(raw_event, day_start, day_end)
            if parsed is None:
                results['activitywatch_malformed_events'] += 1
                continue
            start, end, data = parsed
            fingerprint = activitywatch_event_fingerprint(source, kind, start, end, data)
            if fingerprint in seen_events:
                results['activitywatch_duplicate_events'] += 1
                continue
            seen_events.add(fingerprint)
            duration = end - start

            if kind == 'window':
                app = data.get('app', 'Unknown')
                title = data.get('title', '')
                results['app_time'][app] += duration
                results['activitywatch_source_app_time'][source][app] += duration
                if title:
                    cleaned_title = clean(title[:100])
                    results['window_details'][app][cleaned_title] += duration
                    results['activitywatch_source_window_details'][source][app][cleaned_title] += duration
                    for ticket in extract_tickets(title):
                        results['activitywatch_source_tickets'][source][ticket] += duration
                if 'GitExtensions' in app:
                    branch_match = re.search(r'rooms \(([^)]+)\)', title)
                    if branch_match:
                        branch = branch_match.group(1)
                        results['branches'][branch] += duration
                        results['activitywatch_source_branches'][source][branch] += duration
                    branch_match = re.search(r'Commit to ([^ ]+)', title)
                    if branch_match:
                        branch = branch_match.group(1)
                        results['branches'][branch] += duration
                        results['activitywatch_source_branches'][source][branch] += duration
                if 'ms-teams' in app.lower():
                    results['teams'][title[:100]] += duration
                    results['activitywatch_source_teams'][source][title[:100]] += duration
            elif kind == 'web':
                url = data.get('url', '')
                title = data.get('title', '')
                if url:
                    domain = urlparse(url).netloc
                    results['domain_time'][domain] += duration
                    results['activitywatch_source_domain_time'][source][domain] += duration
                if title:
                    results['page_details'][clean(title[:80])] += duration
                for ticket in extract_tickets(title, url):
                    results['jira_tickets'][ticket] += duration
                    results['activitywatch_source_tickets'][source][ticket] += duration
            elif kind == 'editor':
                file = data.get('file', '')
                if file:
                    results['file_time'][file] += duration
                    results['activitywatch_source_file_time'][source][file] += duration
            elif kind == 'afk' and data.get('status') == 'not-afk':
                interval = (start, end)
                raw_not_afk_intervals.append(interval)
                source_not_afk[source].append(interval)

    merged_not_afk = merge_intervals(raw_not_afk_intervals)
    source_active = {
        source: interval_total_seconds(intervals)
        for source, intervals in source_not_afk.items()
    }
    results['activitywatch_source_active'] = source_active
    results['activitywatch_bucket_counts'] = {
        source: dict(sorted(counts.items())) for source, counts in sorted(bucket_counts.items())
    }
    results['raw_total_active'] = sum(end - start for start, end in raw_not_afk_intervals)
    results['total_active'] = interval_total_seconds(merged_not_afk)
    results['activitywatch_cross_source_overlap'] = max(
        0, sum(source_active.values()) - results['total_active']
    )

    for start, end in merged_not_afk:
        duration = end - start
        timestamp = datetime.fromtimestamp(start, timezone)
        results['active_intervals'].append((timestamp, duration))
        if duration >= 300:
            results['active_periods'].append((timestamp, duration))
    results['active_periods'].sort()

    server_source = normalize_activitywatch_source(
        str(server_info.get('hostname') or ''), source_aliases
    )
    if not relevant_buckets:
        results['activitywatch_warnings'].append('No supported ActivityWatch buckets were discovered')
    if results['activitywatch_sources'] and not any(
        source != server_source for source in results['activitywatch_sources']
    ):
        results['activitywatch_warnings'].append(
            'No remote ActivityWatch source is currently visible through the central API'
        )
    missing_expected_sources = sorted(set(expected_sources) - set(results['activitywatch_sources']))
    if missing_expected_sources:
        results['activitywatch_warnings'].append(
            'Expected ActivityWatch sources are absent: ' + ', '.join(missing_expected_sources)
        )
    for source in results['activitywatch_sources']:
        missing = {'afk', 'window'} - bucket_types_by_source[source]
        if missing:
            results['activitywatch_warnings'].append(
                f"Source {source} is missing expected desktop bucket types: {', '.join(sorted(missing))}"
            )
    if results['activitywatch_duplicate_events']:
        results['activitywatch_warnings'].append(
            f"Ignored {results['activitywatch_duplicate_events']} exact replicated events"
        )
    if results['activitywatch_malformed_events']:
        results['activitywatch_warnings'].append(
            f"Ignored {results['activitywatch_malformed_events']} malformed events"
        )
    return results


def activitywatch_health(client):
    """Return discovery-only server and bucket diagnostics without modifying data."""
    info = client.get_info()
    buckets = client.get_buckets()
    relevant = [bucket for bucket in buckets if bucket.get('type') in SUPPORTED_BUCKET_TYPES]
    source_aliases = get_activitywatch_source_aliases()
    expected_sources = get_activitywatch_expected_sources(source_aliases)
    sources = defaultdict(lambda: defaultdict(list))
    duplicate_candidates = defaultdict(list)
    used_source_aliases = {}

    def latest_update(bucket):
        metadata = bucket.get('metadata')
        metadata_end = metadata.get('end') if isinstance(metadata, dict) else None
        return str(bucket.get('last_updated') or metadata_end or '')

    source_latest_updates = {}
    for bucket in relevant:
        raw_source = raw_activitywatch_source(bucket)
        source = normalize_activitywatch_source(raw_source, source_aliases)
        if raw_source != source:
            used_source_aliases[raw_source] = source
        kind = SUPPORTED_BUCKET_TYPES[bucket['type']]
        sources[source][kind].append(bucket['id'])
        duplicate_candidates[(source, logical_bucket_id(bucket['id']))].append(bucket['id'])
        source_latest_updates[source] = max(
            source_latest_updates.get(source, ''), latest_update(bucket)
        )

    coverage_warnings = []
    for source, kinds in sorted(sources.items()):
        missing = {'afk', 'window'} - set(kinds)
        if missing:
            coverage_warnings.append(
                f"Source {source} is missing expected desktop bucket types: "
                + ', '.join(sorted(missing))
            )
    missing_expected_sources = sorted(set(expected_sources) - set(sources))
    if missing_expected_sources:
        coverage_warnings.append(
            'Expected ActivityWatch sources are absent: ' + ', '.join(missing_expected_sources)
        )

    return {
        'info': info,
        'sources': {source: dict(kinds) for source, kinds in sorted(sources.items())},
        'source_aliases': dict(
            sorted(used_source_aliases.items(), key=lambda item: item[0].casefold())
        ),
        'expected_sources': expected_sources,
        'source_latest_updates': dict(sorted(source_latest_updates.items())),
        'coverage_warnings': coverage_warnings,
        'duplicate_bucket_candidates': [
            ids for ids in duplicate_candidates.values() if len(ids) > 1
        ],
        'latest_bucket_update': max((latest_update(bucket) for bucket in relevant), default=''),
    }


def print_activitywatch_health(health):
    """Print compact central-server discovery diagnostics."""
    info = health['info']
    print(f"ActivityWatch {info.get('version', 'unknown')} at {info.get('hostname', 'unknown')}")
    print(f"Device ID: {info.get('device_id', 'unknown')}")
    print(f"Latest bucket update: {health['latest_bucket_update'] or 'unknown'}")
    if not health['sources']:
        print('Sources: none')
    for source, kinds in health['sources'].items():
        summary = ', '.join(f"{kind}={len(ids)}" for kind, ids in sorted(kinds.items()))
        latest = health['source_latest_updates'].get(source) or 'unknown'
        print(f"Source {source}: {summary}, latest={latest}")
    if health['source_aliases']:
        alias_summary = ', '.join(
            f"{raw} -> {canonical}"
            for raw, canonical in health['source_aliases'].items()
        )
        print(f"Source aliases: {alias_summary}")
    if health['duplicate_bucket_candidates']:
        print(f"Warning: {len(health['duplicate_bucket_candidates'])} duplicate bucket candidate group(s)")
    for warning in health['coverage_warnings']:
        print(f"Warning: {warning}")
    server_source = normalize_activitywatch_source(
        str(info.get('hostname') or ''), get_activitywatch_source_aliases()
    )
    if health['sources'] and not any(source != server_source for source in health['sources']):
        print('Warning: no remote source is visible; discovery-only mode cannot prove completeness')


def find_codex_state_db(codex_home):
    """Find the newest supported Codex state database."""
    candidates = [
        codex_home / 'state_5.sqlite',
        codex_home / 'sqlite' / 'state_5.sqlite',
    ]
    existing = [path for path in candidates if path.exists()]
    return max(existing, key=lambda path: path.stat().st_mtime) if existing else None


def read_codex_rollout_activity(rollout_path, day_start, day_end):
    """Read task turns intersecting one local day from a Codex rollout JSONL file."""
    starts = {}
    finished_spans = []
    day_event_times = []
    last_event_time = None
    latest_user_message = ''
    latest_user_time = 0
    latest_outcome = ''
    latest_outcome_time = 0

    try:
        with rollout_path.open('r', encoding='utf-8', errors='replace') as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    # The currently running task can leave a partial final line while appending.
                    continue

                timestamp = parse_codex_timestamp(entry.get('timestamp'))
                if timestamp is not None:
                    last_event_time = max(last_event_time or timestamp, timestamp)
                    if day_start <= timestamp < day_end:
                        day_event_times.append(timestamp)

                if entry.get('type') != 'event_msg':
                    continue
                payload = entry.get('payload') or {}
                event_type = payload.get('type')
                turn_id = payload.get('turn_id')

                if event_type == 'task_started':
                    started_at = parse_codex_timestamp(payload.get('started_at')) or timestamp
                    if started_at is not None:
                        starts[turn_id or f'unknown-{started_at}'] = started_at
                elif event_type in ('task_complete', 'turn_aborted'):
                    started_at = parse_codex_timestamp(payload.get('started_at'))
                    if started_at is None:
                        started_at = starts.get(turn_id)
                    completed_at = parse_codex_timestamp(payload.get('completed_at')) or timestamp
                    if started_at is not None and completed_at is not None:
                        status = 'completed' if event_type == 'task_complete' else 'aborted'
                        finished_spans.append((started_at, completed_at, status))
                    if turn_id in starts:
                        del starts[turn_id]

                    outcome = payload.get('last_agent_message') or ''
                    if completed_at is not None and completed_at >= latest_outcome_time:
                        latest_outcome = outcome
                        latest_outcome_time = completed_at
                elif event_type == 'user_message' and timestamp is not None:
                    message = payload.get('message')
                    if message and day_start <= timestamp < day_end and timestamp >= latest_user_time:
                        latest_user_message = message
                        latest_user_time = timestamp

    except (OSError, UnicodeError):
        return None

    spans = list(finished_spans)
    for started_at in starts.values():
        if last_event_time is not None and last_event_time >= started_at:
            spans.append((started_at, last_event_time, 'active'))

    intersecting = []
    for started_at, completed_at, status in spans:
        if completed_at >= day_start and started_at < day_end:
            intersecting.append((max(started_at, day_start), min(completed_at, day_end), status))

    # Older rollout formats may not have task lifecycle events. Preserve their
    # semantic evidence using the first/last event seen on the requested day.
    if not intersecting and day_event_times:
        first_event = min(day_event_times)
        last_event = max(day_event_times)
        intersecting.append((first_event, max(first_event, last_event), 'unknown'))

    if not intersecting:
        return None

    merged = merge_intervals((start, end) for start, end, _ in intersecting)
    if not merged and day_event_times:
        instant = min(day_event_times)
        merged = [(instant, instant)]

    latest_span = max(intersecting, key=lambda span: span[1])
    return {
        'spans': merged,
        'status': latest_span[2],
        'turn_count': len(intersecting),
        'latest_user_message': latest_user_message,
        'outcome': latest_outcome if day_start <= latest_outcome_time < day_end else '',
    }


def analyze_codex_history(codex_home, target_date, active_intervals=()):
    """Load root Codex tasks active on the requested local date.

    Codex task spans are semantic evidence and may overlap. ActivityWatch AFK
    intervals remain the duration anchor; subagent and automation threads are
    deliberately excluded to avoid double counting delegated work.
    """
    state_db = find_codex_state_db(codex_home)
    if not state_db:
        return []

    day_start, day_end = day_epoch_bounds(target_date)
    active_epoch_intervals = [
        (start.timestamp(), start.timestamp() + duration)
        for start, duration in active_intervals
        if duration > 0
    ]

    database_uri = f"file:{quote(state_db.resolve().as_posix(), safe='/:')}?mode=ro"
    try:
        connection = sqlite3.connect(database_uri, uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        columns = {row[1] for row in connection.execute('PRAGMA table_info(threads)')}
        required = {'id', 'rollout_path', 'created_at', 'updated_at', 'cwd', 'title'}
        if not required.issubset(columns):
            connection.close()
            return []

        optional_columns = ['name', 'first_user_message', 'git_branch', 'git_origin_url',
                            'thread_source', 'agent_path', 'source']
        select_columns = list(required)
        select_columns.extend(
            column if column in columns else f'NULL AS {column}'
            for column in optional_columns
        )
        rows = connection.execute(
            f"SELECT {', '.join(select_columns)} FROM threads "
            "WHERE updated_at >= ? AND created_at < ? ORDER BY updated_at",
            (int(day_start), int(day_end)),
        ).fetchall()
        connection.close()
    except (sqlite3.Error, OSError):
        return []

    tasks = []
    for row in rows:
        source = str(row['source'] or '')
        if row['thread_source'] in ('subagent', 'automation'):
            continue
        if row['agent_path'] or '"subagent"' in source:
            continue

        rollout_value = normalize_codex_path(row['rollout_path'])
        if not rollout_value:
            continue
        activity = read_codex_rollout_activity(Path(rollout_value), day_start, day_end)
        if not activity:
            continue

        title = compact_codex_text(
            row['name'] or row['title'] or row['first_user_message'],
            limit=180,
        )
        latest_user_message = compact_codex_text(activity['latest_user_message'], limit=140)
        outcome = compact_codex_text(activity['outcome'], limit=200)
        branch = compact_codex_text(row['git_branch'], limit=100)
        spans = activity['spans']
        span_seconds = sum(end - start for start, end in spans)
        active_seconds = interval_overlap_seconds(spans, active_epoch_intervals)

        tasks.append({
            'thread_id': row['id'],
            'workspace': codex_workspace_name(row['cwd']),
            'cwd': normalize_codex_path(row['cwd']),
            'title': title or latest_user_message or '(untitled Codex task)',
            'latest_user_message': latest_user_message,
            'outcome': outcome,
            'branch': branch,
            # Agent outcomes can mention adjacent or compared tickets. User task
            # context and the checked-out branch are safer attribution signals.
            'tickets': extract_tickets(title, latest_user_message, branch),
            'start': datetime.fromtimestamp(spans[0][0]),
            'end': datetime.fromtimestamp(spans[-1][1]),
            'spans': spans,
            'span_seconds': span_seconds,
            'active_seconds': active_seconds,
            'turn_count': activity['turn_count'],
            'status': activity['status'],
        })

    return sorted(tasks, key=lambda task: (task['start'], task['workspace'], task['title']))


def detect_clients(results):
    """Detect which clients were worked on based on domains."""
    clients = CONFIG.get('clients', {})
    detected = defaultdict(float)
    for domain, dur in results['domain_time'].items():
        for key, name in clients.items():
            if key in domain.lower():
                detected[name] += dur
    return detected


def apply_correlations(name):
    """Apply correlations and contacts to map names to clients/groups."""
    contacts = CONFIG.get('contacts', {})
    correlations = CONFIG.get('correlations', {})
    name_norm = normalize_for_match(name)

    # First check correlations (higher priority - maps to client groups)
    for group, related in correlations.items():
        group_norm = normalize_for_match(group)
        # Check if group name appears in the string
        if group_norm in name_norm:
            # Find if any related contact is also mentioned
            for item in related:
                if normalize_for_match(item) in name_norm:
                    return group.title(), item
            return group.title(), None
        # Check if any related item appears
        for item in related:
            if normalize_for_match(item) in name_norm:
                return group.title(), item

    # Then check contacts (maps people to their organizations)
    for contact, client in contacts.items():
        if normalize_for_match(contact) in name_norm:
            # Skip internal contacts for grouping purposes
            if 'internal' not in client.lower():
                return client, contact
            else:
                # Return special marker for internal
                return '__internal__', contact

    return None, None


def categorize_activities(results):
    """Categorize activities into worklog buckets."""
    categories = defaultdict(lambda: {'time': 0, 'items': []})

    # App-based categorization
    app_categories = {
        'rider64.exe': 'Development',
        'Cursor.exe': 'Development',
        'Code.exe': 'Development',
        'devenv.exe': 'Development',
        'datagrip64.exe': 'Development',
        'GitExtensions.exe': 'Development',
        'ChatGPT.exe': 'Development',
        'Codex.exe': 'Development',
        'ms-teams.exe': 'Meetings',
        'ScreenConnect.WindowsClient.exe': 'Support',
        'mstsc.exe': 'Support',
        'olk.exe': 'Administrative',
        'OUTLOOK.EXE': 'Administrative',
    }

    # Categorize app time
    for app, seconds in results['app_time'].items():
        if app in ['LockApp.exe', 'explorer.exe', 'ShellExperienceHost.exe']:
            continue  # Skip system apps

        category = app_categories.get(app, None)
        if category:
            categories[category]['time'] += seconds

    # Categorize JIRA tickets
    ticket_prefixes = CONFIG.get('ticket_prefixes', {})
    known_tickets = CONFIG.get('known_tickets', {})

    for ticket, dur in results['jira_tickets'].items():
        desc = known_tickets.get(ticket, '')
        if ticket.startswith('ROMSD'):
            cat = 'Bug Fix / Support'
        elif ticket.startswith('ITEM'):
            cat = 'Development'
        else:
            cat = 'Other'

        categories[cat]['items'].append({
            'ticket': ticket,
            'description': desc,
            'time': dur
        })

    # Categorize Teams meetings with correlation support
    contacts = CONFIG.get('contacts', {})
    correlations = CONFIG.get('correlations', {})
    meetings_grouped = defaultdict(lambda: {'time': 0, 'contact': None, 'details': []})

    for convo, dur in results['teams'].items():
        client, contact = apply_correlations(convo)

        if client == '__internal__':
            # Internal contact identified
            meetings_grouped['internal']['time'] += dur
            meetings_grouped['internal']['client'] = 'Internal'
            meetings_grouped['internal']['contact'] = contact
            meetings_grouped['internal']['details'].append(convo)
        elif client:
            # External correlation found
            key = client.lower()
            meetings_grouped[key]['time'] += dur
            meetings_grouped[key]['client'] = client
            if contact and not meetings_grouped[key]['contact']:
                meetings_grouped[key]['contact'] = contact
            meetings_grouped[key]['details'].append(convo)
        else:
            # Unknown meeting - keep as separate entry
            # Extract meaningful name from title
            clean_name = convo.split('|')[0].strip()[:40]
            key = clean_name.lower().replace(' ', '_')[:20]
            meetings_grouped[key]['time'] += dur
            meetings_grouped[key]['client'] = clean_name
            meetings_grouped[key]['details'].append(convo)

    # Detect infrastructure work from domains
    infra_domains = ['deploy.3vrooms.app', 'argocd', 'azure', 'github.com']
    infra_time = 0
    for domain, dur in results['domain_time'].items():
        for infra in infra_domains:
            if infra in domain.lower():
                infra_time += dur
                break

    if infra_time > 60:
        categories['Infrastructure']['time'] += infra_time

    return categories, meetings_grouped


def codex_task_time_text(task):
    """Format a Codex task's raw timing evidence without implying billable time."""
    parts = [f"{format_duration(task['span_seconds'])} span"]
    if task['active_seconds'] >= 1:
        parts.append(f"{format_duration(task['active_seconds'])} not-AFK overlap")
    return ' / '.join(parts)


def print_codex_tasks_ai(tasks):
    """Print compact Codex task evidence for AI worklog interpretation."""
    if not tasks:
        return

    print("\n**Codex Tasks (semantic context; spans and overlaps may overlap):**")
    for task in tasks:
        period = f"{task['start'].strftime('%H:%M')}-{task['end'].strftime('%H:%M')}"
        context = [task['workspace']]
        if task['tickets']:
            context.append(', '.join(task['tickets']))
        if task['branch']:
            context.append(f"branch {task['branch']}")
        context.append(task['status'])
        context.append(codex_task_time_text(task))
        print(f"- [{period}] {' | '.join(context)} | {task['title']}")
        if task['outcome'] and task['outcome'].lower() != task['title'].lower():
            print(f"  Outcome: {task['outcome']}")


def print_activitywatch_provenance(results, markdown=True):
    """Render device discovery and overlap diagnostics."""
    server = results.get('activitywatch_server', {})
    sources = results.get('activitywatch_sources', [])
    source_active = results.get('activitywatch_source_active', {})
    bucket_counts = results.get('activitywatch_bucket_counts', {})
    source_aliases = results.get('activitywatch_source_aliases', {})
    source_text = ', '.join(
        f"{source} ({format_duration(source_active.get(source, 0))} active)"
        for source in sources
    ) or 'none'
    bucket_text = '; '.join(
        f"{source}: " + ', '.join(
            f"{kind}={count}" for kind, count in sorted(counts.items())
        )
        for source, counts in sorted(bucket_counts.items())
    ) or 'none'
    server_text = (
        f"{server.get('hostname', 'unknown')} "
        f"({server.get('version', 'unknown')})"
    )
    alias_text = ', '.join(
        f"{raw} -> {canonical}" for raw, canonical in source_aliases.items()
    )
    overlap = results.get('activitywatch_cross_source_overlap', 0)
    if markdown:
        print(f"**ActivityWatch Server: {server_text}**")
        print(f"**ActivityWatch Sources: {source_text}**")
        if alias_text:
            print(f"**ActivityWatch Source aliases: {alias_text}**")
        print(f"**ActivityWatch Buckets: {bucket_text}**")
        if overlap >= 1:
            print(f"**Cross-source interaction overlap: {format_duration(overlap)}**")
        for warning in results.get('activitywatch_warnings', []):
            print(f"**ActivityWatch Warning: {warning}**")
    else:
        print(f"ActivityWatch Server: {server_text}")
        print(f"ActivityWatch Sources: {source_text}")
        if alias_text:
            print(f"ActivityWatch Source aliases: {alias_text}")
        print(f"ActivityWatch Buckets: {bucket_text}")
        if overlap >= 1:
            print(f"Cross-source interaction overlap: {format_duration(overlap)}")
        for warning in results.get('activitywatch_warnings', []):
            print(f"Warning: {warning}")


def activitywatch_ticket_sources(results, ticket):
    """Return canonical ActivityWatch sources containing evidence for a ticket."""
    sources = set()
    for source, tickets in results.get('activitywatch_source_tickets', {}).items():
        if tickets.get(ticket, 0) > 0:
            sources.add(source)
    for source, branches in results.get('activitywatch_source_branches', {}).items():
        if any(ticket.casefold() in branch.casefold() for branch in branches):
            sources.add(source)
    return sorted(sources, key=str.casefold)


def format_evidence_sources(sources, fallback='device unknown (non-ActivityWatch evidence)'):
    return ', '.join(sorted(set(sources), key=str.casefold)) or fallback


def print_activitywatch_source_evidence(results):
    """Render compact task-attribution evidence grouped by recording source."""
    sources = results.get('activitywatch_sources', [])
    if not sources:
        return

    print("\n**ActivityWatch Evidence by Source:**")
    for source in sources:
        parts = []
        tickets = results.get('activitywatch_source_tickets', {}).get(source, {})
        if tickets:
            parts.append('tickets ' + ', '.join(sorted(tickets)))
        apps = results.get('activitywatch_source_app_time', {}).get(source, {})
        top_apps = [
            f"{app} {format_duration(seconds)}"
            for app, seconds in sorted(apps.items(), key=lambda item: -item[1])
            if seconds >= 60
        ][:6]
        if top_apps:
            parts.append('apps ' + ', '.join(top_apps))
        domains = results.get('activitywatch_source_domain_time', {}).get(source, {})
        top_domains = [
            f"{domain} {format_duration(seconds)}"
            for domain, seconds in sorted(domains.items(), key=lambda item: -item[1])
            if domain and seconds >= 60
        ][:4]
        if top_domains:
            parts.append('domains ' + ', '.join(top_domains))
        print(f"- {source}: {' | '.join(parts) if parts else 'no task-specific foreground evidence'}")


def print_ai_summary_v2(results, target_date):
    """Print categorized AI-friendly summary with correlations applied."""
    total_hours = results['total_active'] / 3600
    date_str = target_date.strftime('%Y-%m-%d')

    categories, meetings = categorize_activities(results)

    print(f"# Worklog Data for {date_str}")
    print(f"**Observed Interaction: {total_hours:.1f}h**")
    print_activitywatch_provenance(results)
    print_activitywatch_source_evidence(results)
    raw_total = results.get('raw_total_active', results['total_active'])
    overlap_removed = max(0, raw_total - results['total_active'])
    if overlap_removed >= 1:
        print(f"**Merged not-AFK overlap removed: {format_duration(overlap_removed)}**")
    evidence_union = results.get('evidence_union_seconds', results['total_active'])
    codex_union_count = len(results.get('evidence_union_codex_task_ids', []))
    print(
        f"**Evidence Union Candidate: {evidence_union / 3600:.1f}h "
        f"(merged ActivityWatch + {codex_union_count} qualifying Codex root tasks; validate extensions)**"
    )

    if results['active_periods']:
        first = results['active_periods'][0][0].strftime('%H:%M')
        last_start, last_duration = results['active_periods'][-1]
        last = (last_start + timedelta(seconds=last_duration)).strftime('%H:%M')
        print(f"**Window: {first} - {last}**")

        # Detect breaks (gaps > 15 minutes between active periods)
        breaks = []
        for i in range(len(results['active_periods']) - 1):
            period_start, period_duration = results['active_periods'][i]
            period_end = period_start + timedelta(seconds=period_duration)
            next_start = results['active_periods'][i + 1][0]
            gap = (next_start - period_end).total_seconds()
            if gap > 900:  # > 15 minutes
                breaks.append((period_end.strftime('%H:%M'), next_start.strftime('%H:%M'), gap))
        if breaks:
            break_strs = [f"{s} - {e} ({format_duration(d)})" for s, e, d in breaks]
            print(f"**Breaks: {', '.join(break_strs)}**")

    # Detected clients
    detected_clients = detect_clients(results)
    if detected_clients:
        print(f"**Clients: {', '.join(f'{c} ({format_duration(d)})' for c, d in sorted(detected_clients.items(), key=lambda x: -x[1]))}**")

    print("\n## Categorized Summary")
    print("\n| Category | Client/Ticket | Source | Description | Time |")
    print("|----------|---------------|--------|-------------|------|")

    known_tickets = CONFIG.get('known_tickets', {})

    # Collect JIRA tickets from multiple sources
    all_tickets = defaultdict(float)
    codex_ticket_times = defaultdict(float)

    # 1. From browser activity
    for ticket, dur in results['jira_tickets'].items():
        all_tickets[ticket] = max(all_tickets[ticket], dur)

    # 2. From git branches (just the branch interaction time, not estimated dev time)
    for branch, dur in results['branches'].items():
        ticket_match = re.search(r'(ITEM-\d+|ROMSD-\d+)', branch, re.IGNORECASE)
        if ticket_match:
            ticket = ticket_match.group(1).upper()
            all_tickets[ticket] = max(all_tickets[ticket], dur)

    # 3. From window titles
    for app, titles in results['window_details'].items():
        for title, dur in titles.items():
            for match in re.findall(r'(ITEM-\d+|ROMSD-\d+)', title, re.IGNORECASE):
                ticket = match.upper()
                all_tickets[ticket] = max(all_tickets[ticket], dur)

    # 4. From root Codex tasks. Active overlap is supporting evidence only and
    # may overlap other Codex tasks, so retain the largest signal per ticket.
    for task in results.get('codex_tasks', []):
        for ticket in task['tickets']:
            codex_ticket_times[ticket] = max(codex_ticket_times[ticket], task['active_seconds'])
            all_tickets[ticket] = max(all_tickets[ticket], task['active_seconds'])

    # ITEM tickets (features) - show raw detected time
    item_tickets = [(t, d) for t, d in all_tickets.items() if t.startswith('ITEM')]
    for ticket, dur in sorted(item_tickets, key=lambda x: -x[1]):
        if dur >= 60 or ticket in codex_ticket_times:
            desc = known_tickets.get(ticket, '')
            raw_time = f"{format_duration(dur)} (raw, Codex context)" if dur >= 60 else "Codex history"
            source = format_evidence_sources(activitywatch_ticket_sources(results, ticket))
            print(f"| Development | [{ticket}](https://3volutions.atlassian.net/browse/{ticket}) | {source} | {desc} | {raw_time} |")

    # ROMSD tickets (bugs/support)
    romsd_tickets = [(t, d) for t, d in all_tickets.items() if t.startswith('ROMSD')]
    for ticket, dur in sorted(romsd_tickets, key=lambda x: -x[1]):
        if dur >= 60 or ticket in codex_ticket_times:
            desc = known_tickets.get(ticket, '')
            raw_time = f"{format_duration(dur)} (raw, Codex context)" if dur >= 60 else "Codex history"
            source = format_evidence_sources(activitywatch_ticket_sources(results, ticket))
            print(f"| Bug Fix | [{ticket}](https://3volutions.atlassian.net/browse/{ticket}) | {source} | {desc} | {raw_time} |")

    # Meetings (grouped by client with correlations)
    for key, meeting in sorted(meetings.items(), key=lambda x: -x[1]['time']):
        if meeting['time'] >= 60:
            # Skip generic entries like Chat, Calendar
            if key in ['chat', 'calendar', 'general']:
                continue

            client = meeting.get('client', key.title())
            contact = meeting.get('contact')
            if contact:
                client_str = f"{client} ({contact})"
            else:
                client_str = client

            # Extract clean description from details
            if meeting['details']:
                detail = meeting['details'][0]
                # Get the meeting name part (before | separator)
                desc = detail.split('|')[0].strip()[:50]
            else:
                desc = '-'
            meeting_sources = [
                source
                for source, conversations in results.get('activitywatch_source_teams', {}).items()
                if any(detail in conversations for detail in meeting['details'])
            ]
            print(f"| Meeting | {client_str} | {format_evidence_sources(meeting_sources)} | {desc} | {format_duration(meeting['time'])} |")

    # Infrastructure
    if categories.get('Infrastructure', {}).get('time', 0) >= 60:
        infra_sources = [
            source
            for source, domains in results.get('activitywatch_source_domain_time', {}).items()
            if any(
                marker in domain.lower()
                for domain in domains
                for marker in ['deploy.3vrooms.app', 'argocd', 'azure', 'github.com']
            )
        ]
        print(f"| Infrastructure | - | {format_evidence_sources(infra_sources)} | DevOps, deployments, CI/CD | {format_duration(categories['Infrastructure']['time'])} |")

    # Administrative
    admin_time = results['app_time'].get('olk.exe', 0) + results['app_time'].get('OUTLOOK.EXE', 0)
    if admin_time >= 60:
        admin_sources = [
            source
            for source, apps in results.get('activitywatch_source_app_time', {}).items()
            if apps.get('olk.exe', 0) + apps.get('OUTLOOK.EXE', 0) >= 60
        ]
        print(f"| Administrative | - | {format_evidence_sources(admin_sources)} | Email, calendar | {format_duration(admin_time)} |")

    # Raw data section for AI interpretation
    print("\n## Raw Data (for time estimation)")

    # App times - critical for estimating actual work time
    print("\n**App Times:**")
    dev_apps = [
        'rider64.exe', 'Cursor.exe', 'Code.exe', 'WindowsTerminal.exe',
        'GitExtensions.exe', 'datagrip64.exe', *CODEX_APP_NAMES,
    ]
    for app in dev_apps:
        if app in results['app_time'] and results['app_time'][app] >= 60:
            print(f"- {app}: {format_duration(results['app_time'][app])}")

    # Git branches (indicates what was worked on)
    if results['branches']:
        branches_over_1m = [(b, d) for b, d in results['branches'].items() if d >= 60]
        if branches_over_1m:
            print("\n**Git Branches:**")
            for branch, dur in sorted(branches_over_1m, key=lambda x: -x[1]):
                ticket_match = re.search(r'(ITEM-\d+|ROMSD-\d+)', branch, re.IGNORECASE)
                ticket_hint = f" → {ticket_match.group(1)}" if ticket_match else ""
                print(f"- {branch[:60]}: {format_duration(dur)}{ticket_hint}")

    # Files edited
    if results['file_time']:
        files_over_1m = [(f, d) for f, d in results['file_time'].items() if d >= 60]
        if files_over_1m:
            print("\n**Files Edited:**")
            for f, dur in sorted(files_over_1m, key=lambda x: -x[1])[:10]:
                filename = Path(f).name if '\\' in f or '/' in f else f
                print(f"- {filename}: {format_duration(dur)}")

    print_codex_tasks_ai(results.get('codex_tasks', []))

    # Window titles for context
    print("\n**Window Context:**")
    for app in ['rider64.exe', 'Cursor.exe', 'WindowsTerminal.exe', *CODEX_APP_NAMES]:
        if app in results['window_details']:
            titles = results['window_details'][app]
            relevant = [(t, d) for t, d in titles.items() if d >= 60]
            if relevant:
                print(f"\n*{app}:*")
                for title, dur in sorted(relevant, key=lambda x: -x[1])[:4]:
                    print(f"- [{format_duration(dur)}] {clean(title[:70])}")

    print("\n---")
    if results.get('codex_tasks'):
        print("Use App Times + Git Branches + Codex task context to estimate development time per ticket.")
        print("Codex spans are not billable durations; anchor estimates in ActivityWatch not-AFK time and git evidence.")
    else:
        print("Use App Times + Git Branches to estimate development time per ticket.")


def print_ai_summary(results, target_date):
    """Print compact AI-friendly summary for interpretation."""
    total_hours = results['total_active'] / 3600
    date_str = target_date.strftime('%Y-%m-%d')

    print(f"# ActivityWatch Data for {date_str}")
    print(f"**Observed Interaction: {total_hours:.1f}h**")

    if results['active_periods']:
        first = results['active_periods'][0][0].strftime('%H:%M')
        last_start, last_duration = results['active_periods'][-1]
        last = (last_start + timedelta(seconds=last_duration)).strftime('%H:%M')
        print(f"**Window: {first} - {last}**")

    # Detected clients
    detected_clients = detect_clients(results)
    if detected_clients:
        print(f"**Clients: {', '.join(f'{c} ({format_duration(d)})' for c, d in sorted(detected_clients.items(), key=lambda x: -x[1]))}**")

    # Apps
    print("\n## Apps")
    for app, seconds in sorted(results['app_time'].items(), key=lambda x: -x[1])[:10]:
        if seconds >= 120:
            print(f"- {app}: {format_duration(seconds)}")

    # JIRA with ticket type hints
    if results['jira_tickets']:
        print("\n## JIRA Tickets")
        prefixes = CONFIG.get('ticket_prefixes', {})
        for ticket, dur in sorted(results['jira_tickets'].items(), key=lambda x: -x[1]):
            if dur >= 60:
                hint = ""
                for prefix, desc in prefixes.items():
                    if ticket.startswith(prefix):
                        hint = f" ({desc})"
                        break
                known = CONFIG.get('known_tickets', {}).get(ticket, "")
                if known:
                    hint = f" - {known}"
                print(f"- {ticket}: {format_duration(dur)}{hint}")

    # Window context
    print("\n## Window Titles (context)")
    for app in sorted(results['app_time'].keys(), key=lambda x: -results['app_time'][x])[:8]:
        titles = results['window_details'][app]
        relevant_titles = [(t, d) for t, d in titles.items() if d >= 60]
        if relevant_titles and results['app_time'][app] >= 180:
            print(f"\n**{app}**")
            for title, dur in sorted(relevant_titles, key=lambda x: -x[1])[:4]:
                print(f"- [{format_duration(dur)}] {title[:80]}")

    # Files
    if results['file_time']:
        files_over_1m = [(f, d) for f, d in results['file_time'].items() if d >= 60]
        if files_over_1m:
            print("\n## Files Edited")
            projects = CONFIG.get('projects', {})
            for f, dur in sorted(files_over_1m, key=lambda x: -x[1])[:12]:
                hint = ""
                for proj_key, proj_name in projects.items():
                    if proj_key in f.lower():
                        hint = f" [{proj_name}]"
                        break
                print(f"- {clean(f)}: {format_duration(dur)}{hint}")

    # Branches
    if results['branches']:
        branches_over_1m = [(b, d) for b, d in results['branches'].items() if d >= 60]
        if branches_over_1m:
            print("\n## Git Branches")
            for branch, dur in sorted(branches_over_1m, key=lambda x: -x[1]):
                print(f"- {branch[:70]}: {format_duration(dur)}")

    # Domains with context hints
    if results['domain_time']:
        print("\n## Web Domains")
        context_hints = CONFIG.get('context_hints', {})
        environments = CONFIG.get('environments', {})
        for domain, dur in sorted(results['domain_time'].items(), key=lambda x: -x[1])[:10]:
            if dur >= 60 and domain:
                hint = ""
                for env_domain, env_name in environments.items():
                    if env_domain in domain:
                        hint = f" [{env_name}]"
                        break
                if not hint:
                    for hint_domain, hint_text in context_hints.items():
                        if hint_domain in domain:
                            hint = f" [{hint_text}]"
                            break
                print(f"- {domain}: {format_duration(dur)}{hint}")

    # Teams
    if results['teams']:
        teams_over_1m = [(t, d) for t, d in results['teams'].items() if d >= 60]
        if teams_over_1m:
            print("\n## Teams")
            for t, dur in sorted(teams_over_1m, key=lambda x: -x[1]):
                print(f"- {t}: {format_duration(dur)}")

    # Likely personal
    likely_personal = CONFIG.get('likely_personal', [])
    personal_found = []
    for app, titles in results['window_details'].items():
        for title, dur in titles.items():
            for personal_hint in likely_personal:
                if personal_hint.lower() in title.lower() and dur >= 60:
                    personal_found.append((title[:50], dur))

    if personal_found:
        print("\n## Likely Personal (verify context)")
        for title, dur in personal_found[:5]:
            print(f"- [{format_duration(dur)}] {title}")

    print("\n---")
    print("Categorize into worklog buckets. Check browser activity context - YouTube/t3.chat may be work-related research.")


def print_summary(results, target_date):
    """Print detailed human-readable summary."""
    total_hours = results['total_active'] / 3600
    date_str = target_date.strftime('%Y-%m-%d')

    print("\n" + "=" * 80)
    print(f"WORKLOG SUMMARY - {date_str}")
    print("=" * 80)
    print(f"\nObserved ActivityWatch Interaction: {total_hours:.1f} hours")
    print_activitywatch_provenance(results, markdown=False)

    if results['active_periods']:
        first = results['active_periods'][0][0].strftime('%H:%M')
        last_start, last_duration = results['active_periods'][-1]
        last = (last_start + timedelta(seconds=last_duration)).strftime('%H:%M')
        print(f"Work Window: {first} - {last}")

    # Application time
    print("\n" + "-" * 80)
    print("APPLICATION TIME")
    print("-" * 80)
    for app, seconds in sorted(results['app_time'].items(), key=lambda x: -x[1])[:12]:
        if seconds >= 60:
            print(f"  {format_duration(seconds):>6}  {app}")

    # JIRA tickets
    if results['jira_tickets']:
        print("\n" + "-" * 80)
        print("JIRA TICKETS")
        print("-" * 80)
        for ticket, dur in sorted(results['jira_tickets'].items(), key=lambda x: -x[1]):
            if dur >= 30:
                print(f"  {format_duration(dur):>6}  {ticket}")

    # Top window titles
    print("\n" + "-" * 80)
    print("TOP ACTIVITIES BY APP")
    print("-" * 80)
    for app in sorted(results['app_time'].keys(), key=lambda x: -results['app_time'][x])[:6]:
        titles = results['window_details'][app]
        if titles and results['app_time'][app] >= 300:
            print(f"\n  {app}:")
            for title, dur in sorted(titles.items(), key=lambda x: -x[1])[:3]:
                if dur >= 60:
                    print(f"    [{format_duration(dur):>5}] {title[:70]}")

    # Codex root tasks
    codex_tasks = results.get('codex_tasks', [])
    if codex_tasks:
        print("\n" + "-" * 80)
        print("CODEX TASKS (CONTEXT; SPANS MAY OVERLAP)")
        print("-" * 80)
        for task in codex_tasks:
            period = f"{task['start'].strftime('%H:%M')}-{task['end'].strftime('%H:%M')}"
            tickets = f" [{', '.join(task['tickets'])}]" if task['tickets'] else ''
            print(f"  {period}  {task['workspace']}{tickets}  {task['status']}")
            print(f"           {codex_task_time_text(task)}  {task['title']}")
            if task['outcome'] and task['outcome'].lower() != task['title'].lower():
                print(f"           Outcome: {task['outcome']}")

    # Files
    if results['file_time']:
        print("\n" + "-" * 80)
        print("FILES WORKED ON")
        print("-" * 80)
        for f, dur in sorted(results['file_time'].items(), key=lambda x: -x[1])[:10]:
            if dur >= 60:
                print(f"  {format_duration(dur):>6}  {clean(f)}")

    # Branches
    if results['branches']:
        print("\n" + "-" * 80)
        print("GIT BRANCHES")
        print("-" * 80)
        for branch, dur in sorted(results['branches'].items(), key=lambda x: -x[1]):
            if dur >= 30:
                branch_short = branch[:60] + "..." if len(branch) > 60 else branch
                print(f"  {format_duration(dur):>6}  {branch_short}")

    # Domains
    if results['domain_time']:
        print("\n" + "-" * 80)
        print("WEB DOMAINS")
        print("-" * 80)
        for domain, dur in sorted(results['domain_time'].items(), key=lambda x: -x[1])[:10]:
            if dur >= 60:
                print(f"  {format_duration(dur):>6}  {domain}")

    # Teams
    if results['teams']:
        print("\n" + "-" * 80)
        print("MS TEAMS")
        print("-" * 80)
        for t, dur in sorted(results['teams'].items(), key=lambda x: -x[1]):
            if dur >= 30:
                print(f"  {format_duration(dur):>6}  {t}")

    # Active periods
    print("\n" + "-" * 80)
    print("ACTIVE PERIODS")
    print("-" * 80)
    for ts, dur in results['active_periods']:
        print(f"  {ts.strftime('%H:%M')}  {format_duration(dur)}")

    print("\n" + "=" * 80)
    print("SUGGESTED WORKLOG ENTRIES")
    print("=" * 80)
    print(f"\n  Observed ActivityWatch interaction: {total_hours:.1f}h")
    print("  Determine work duration and billability separately from the combined evidence.")
    print(f"\n  Tip: Run with --ai flag for compact summary to paste to Claude")
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Analyze API-visible ActivityWatch data for worklog generation.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python worklog.py                     Interactive mode
  python worklog.py 27.01.2026          Analyze specific date
  python worklog.py 27.01.2026 --ai     Compact output for AI interpretation
  python worklog.py today               Analyze today
  python worklog.py yesterday           Analyze yesterday
        """
    )
    parser.add_argument('date', nargs='?', help='Date to analyze (e.g., 27.01.2026, today, yesterday)')
    parser.add_argument('--ai', action='store_true', help='Output compact format for AI interpretation')
    parser.add_argument('--aw-host', help='ActivityWatch API host (default: AW_HOST/config/127.0.0.1)')
    parser.add_argument('--aw-port', type=int, help='ActivityWatch API port (default: AW_PORT/config/5600)')
    parser.add_argument(
        '--activitywatch-health', action='store_true',
        help='Show read-only server, source, and bucket discovery diagnostics',
    )
    parser.add_argument('--config', help='Path to config.json')
    parser.add_argument('--codex-home', help='Path to Codex data directory (default: CODEX_HOME or ~/.codex)')
    parser.add_argument('--no-codex', action='store_true', help='Do not include local Codex task history')

    args = parser.parse_args()

    # Load config before resolving ActivityWatch and Codex settings.
    script_dir = Path(__file__).parent
    config_path = Path(args.config) if args.config else script_dir / 'config.json'
    load_config(config_path)

    try:
        aw_host, aw_port, aw_timezone, aw_timeout = get_activitywatch_settings(
            args.aw_host, args.aw_port
        )
        client = ActivityWatchRESTClient(aw_host, aw_port, aw_timeout)
        if args.activitywatch_health:
            print_activitywatch_health(activitywatch_health(client))
            return
    except ActivityWatchAPIError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Get date
    if args.date:
        if args.date.lower() == 'today':
            target_date = datetime.now(aw_timezone).replace(hour=0, minute=0, second=0, microsecond=0)
        elif args.date.lower() == 'yesterday':
            target_date = (datetime.now(aw_timezone) - timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            target_date = parse_date(args.date)
            if not target_date:
                print(f"Error: Invalid date format '{args.date}'")
                print("Use formats like: 2026-01-27, 27.01.2026, today, yesterday")
                sys.exit(1)
    else:
        # Interactive mode
        print("\n" + "=" * 80)
        print("ACTIVITYWATCH WORKLOG ANALYZER (REST API)")
        print("=" * 80)
        print("\nEnter date to analyze (formats: 2026-01-27, 27.01.2026, today, yesterday)")

        while True:
            date_input = input("\nDate: ").strip()
            if not date_input:
                print("No date entered. Exiting.")
                sys.exit(0)

            if date_input.lower() == 'today':
                target_date = datetime.now(aw_timezone).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                break
            elif date_input.lower() == 'yesterday':
                target_date = (datetime.now(aw_timezone) - timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                break
            else:
                target_date = parse_date(date_input)
                if target_date:
                    break
            print("Invalid date format. Please try again.")

    # Analyze
    print(
        f"Querying ActivityWatch at {aw_host}:{aw_port} for "
        f"{target_date.strftime('%Y-%m-%d')} ({aw_timezone})..."
    )
    try:
        results = analyze_day(client, target_date, aw_timezone)
    except ActivityWatchAPIError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not args.no_codex:
        codex_home = get_codex_home(args.codex_home)
        results['codex_tasks'] = analyze_codex_history(
            codex_home,
            target_date,
            results.get('active_intervals', []),
        )

    results.update(calculate_evidence_union(results))

    if results['total_active'] == 0 and not results.get('codex_tasks'):
        print(f"\nNo activity found for {target_date.strftime('%Y-%m-%d')}")
        sys.exit(1)

    if args.ai:
        print_ai_summary_v2(results, target_date)
    else:
        print_summary(results, target_date)


if __name__ == '__main__':
    main()
