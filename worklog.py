#!/usr/bin/env python3
"""
ActivityWatch Worklog Analyzer
Queries ActivityWatch SQLite database directly for faster, real-time analysis.

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
from datetime import datetime, timedelta
from urllib.parse import quote, urlparse
from pathlib import Path, PureWindowsPath

# Configure UTF-8 output for Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Global config
CONFIG = {}

CODEX_APP_NAMES = ['ChatGPT.exe', 'Codex.exe']


def get_db_path():
    """Get database path from config or environment."""
    # 1. Check environment variable
    if os.environ.get('AW_DATABASE'):
        return os.environ['AW_DATABASE']

    # 2. Check config.json
    if CONFIG.get('database'):
        return CONFIG['database']

    # 3. Fallback default
    return r"C:\Users\Lukas\AppData\Local\activitywatch\aw-server-rust\sqlite.db"


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


def day_epoch_bounds(target_date):
    """Return local-day boundaries as Unix timestamps."""
    start = datetime(target_date.year, target_date.month, target_date.day)
    end = start + timedelta(days=1)
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


def get_buckets(cursor):
    """Get all bucket IDs and names."""
    cursor.execute('SELECT id, name, type FROM buckets')
    return {row[1]: {'id': row[0], 'type': row[2]} for row in cursor.fetchall()}


def query_events(cursor, bucket_id, start_ns, end_ns):
    """Query events for a bucket within time range."""
    cursor.execute('''
        SELECT starttime, endtime, data
        FROM events
        WHERE bucketrow = ? AND starttime >= ? AND starttime < ?
        ORDER BY starttime
    ''', (bucket_id, start_ns, end_ns))
    return cursor.fetchall()


def analyze_day(db_path, target_date):
    """Analyze all activity for a given date."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Calculate time range (nanoseconds)
    start_dt = datetime(target_date.year, target_date.month, target_date.day)
    end_dt = start_dt + timedelta(days=1)
    start_ns = int(start_dt.timestamp() * 1_000_000_000)
    end_ns = int(end_dt.timestamp() * 1_000_000_000)

    buckets = get_buckets(cursor)

    results = {
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
    }

    # Window activity
    window_bucket = buckets.get('aw-watcher-window_andromeda')
    if window_bucket:
        events = query_events(cursor, window_bucket['id'], start_ns, end_ns)
        for start, end, data_json in events:
            duration = (end - start) / 1_000_000_000
            data = json.loads(data_json)
            app = data.get('app', 'Unknown')
            title = data.get('title', '')

            results['app_time'][app] += duration
            if title:
                results['window_details'][app][clean(title[:100])] += duration

            # Git branches
            if 'GitExtensions' in app:
                m = re.search(r'rooms \(([^)]+)\)', title)
                if m:
                    results['branches'][m.group(1)] += duration
                m = re.search(r'Commit to ([^ ]+)', title)
                if m:
                    results['branches'][m.group(1)] += duration

            # Teams - keep full title for correlation matching (don't clean yet)
            if 'ms-teams' in app.lower():
                # Store original title for correlation matching
                results['teams'][title[:100]] += duration

    # Web activity (Edge)
    web_bucket = buckets.get('aw-watcher-web-edge_andromeda')
    if web_bucket:
        events = query_events(cursor, web_bucket['id'], start_ns, end_ns)
        for start, end, data_json in events:
            duration = (end - start) / 1_000_000_000
            data = json.loads(data_json)
            url = data.get('url', '')
            title = data.get('title', '')

            if url:
                domain = urlparse(url).netloc
                results['domain_time'][domain] += duration

            if title:
                results['page_details'][clean(title[:80])] += duration

            # JIRA tickets
            matches = re.findall(r'ROMSD-\d+', title + url)
            for m in matches:
                results['jira_tickets'][m] += duration
            matches = re.findall(r'ITEM-\d+', title + url)
            for m in matches:
                results['jira_tickets'][m] += duration

    # Web activity (Firefox)
    web_bucket = buckets.get('aw-watcher-web-firefox_andromeda')
    if web_bucket:
        events = query_events(cursor, web_bucket['id'], start_ns, end_ns)
        for start, end, data_json in events:
            duration = (end - start) / 1_000_000_000
            data = json.loads(data_json)
            url = data.get('url', '')
            title = data.get('title', '')

            if url:
                domain = urlparse(url).netloc
                results['domain_time'][domain] += duration

            if title:
                results['page_details'][clean(title[:80])] += duration

            matches = re.findall(r'ROMSD-\d+', title + url)
            for m in matches:
                results['jira_tickets'][m] += duration
            matches = re.findall(r'ITEM-\d+', title + url)
            for m in matches:
                results['jira_tickets'][m] += duration

    # IDE files (Rider)
    rider_bucket = buckets.get('aw-watcher-jetbrains-rider_andromeda')
    if rider_bucket:
        events = query_events(cursor, rider_bucket['id'], start_ns, end_ns)
        for start, end, data_json in events:
            duration = (end - start) / 1_000_000_000
            data = json.loads(data_json)
            file = data.get('file', '')
            if file:
                results['file_time'][file] += duration

    # IDE files (VSCode)
    vscode_bucket = buckets.get('aw-watcher-vscode_andromeda')
    if vscode_bucket:
        events = query_events(cursor, vscode_bucket['id'], start_ns, end_ns)
        for start, end, data_json in events:
            duration = (end - start) / 1_000_000_000
            data = json.loads(data_json)
            file = data.get('file', '')
            if file:
                results['file_time'][file] += duration

    # AFK status. ActivityWatch history/heartbeat rows can overlap, so never
    # sum them directly; merge them into a non-overlapping union first.
    afk_bucket = buckets.get('aw-watcher-afk_andromeda')
    raw_not_afk_intervals = []
    if afk_bucket:
        events = query_events(cursor, afk_bucket['id'], start_ns, end_ns)
        for start, end, data_json in events:
            data = json.loads(data_json)
            status = data.get('status', '')
            if status == 'not-afk':
                raw_not_afk_intervals.append((start / 1_000_000_000, end / 1_000_000_000))

    merged_not_afk = merge_intervals(raw_not_afk_intervals)
    results['raw_total_active'] = sum(end - start for start, end in raw_not_afk_intervals)
    results['total_active'] = interval_total_seconds(merged_not_afk)
    for start, end in merged_not_afk:
        duration = end - start
        ts = datetime.fromtimestamp(start)
        results['active_intervals'].append((ts, duration))
        if duration >= 300:
            results['active_periods'].append((ts, duration))

    results['active_periods'].sort()
    conn.close()
    return results


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


def print_ai_summary_v2(results, target_date):
    """Print categorized AI-friendly summary with correlations applied."""
    total_hours = results['total_active'] / 3600
    date_str = target_date.strftime('%Y-%m-%d')

    categories, meetings = categorize_activities(results)

    print(f"# Worklog Data for {date_str}")
    print(f"**Observed Interaction: {total_hours:.1f}h**")
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
    print("\n| Category | Client/Ticket | Description | Time |")
    print("|----------|---------------|-------------|------|")

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
            print(f"| Development | [{ticket}](https://3volutions.atlassian.net/browse/{ticket}) | {desc} | {raw_time} |")

    # ROMSD tickets (bugs/support)
    romsd_tickets = [(t, d) for t, d in all_tickets.items() if t.startswith('ROMSD')]
    for ticket, dur in sorted(romsd_tickets, key=lambda x: -x[1]):
        if dur >= 60 or ticket in codex_ticket_times:
            desc = known_tickets.get(ticket, '')
            raw_time = f"{format_duration(dur)} (raw, Codex context)" if dur >= 60 else "Codex history"
            print(f"| Bug Fix | [{ticket}](https://3volutions.atlassian.net/browse/{ticket}) | {desc} | {raw_time} |")

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
            print(f"| Meeting | {client_str} | {desc} | {format_duration(meeting['time'])} |")

    # Infrastructure
    if categories.get('Infrastructure', {}).get('time', 0) >= 60:
        print(f"| Infrastructure | - | DevOps, deployments, CI/CD | {format_duration(categories['Infrastructure']['time'])} |")

    # Administrative
    admin_time = results['app_time'].get('olk.exe', 0) + results['app_time'].get('OUTLOOK.EXE', 0)
    if admin_time >= 60:
        print(f"| Administrative | - | Email, calendar | {format_duration(admin_time)} |")

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
        description='Analyze ActivityWatch SQLite database for worklog generation.',
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
    parser.add_argument('--db', help='Path to SQLite database (default: from config.json or AW_DATABASE env)')
    parser.add_argument('--config', help='Path to config.json')
    parser.add_argument('--codex-home', help='Path to Codex data directory (default: CODEX_HOME or ~/.codex)')
    parser.add_argument('--no-codex', action='store_true', help='Do not include local Codex task history')

    args = parser.parse_args()

    # Load config first (needed for db path)
    script_dir = Path(__file__).parent
    config_path = Path(args.config) if args.config else script_dir / 'config.json'
    load_config(config_path)

    # Database path: CLI arg > env var > config.json > default
    db_path = args.db if args.db else get_db_path()
    if not Path(db_path).exists():
        print(f"Error: Database not found at {db_path}")
        print("\nSet the database path in one of these ways:")
        print("  1. config.json: \"database\": \"path/to/test.db\"")
        print("  2. Environment: set AW_DATABASE=path/to/test.db")
        print("  3. CLI argument: --db path/to/test.db")
        sys.exit(1)

    # Get date
    if args.date:
        if args.date.lower() == 'today':
            target_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        elif args.date.lower() == 'yesterday':
            target_date = (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            target_date = parse_date(args.date)
            if not target_date:
                print(f"Error: Invalid date format '{args.date}'")
                print("Use formats like: 2026-01-27, 27.01.2026, today, yesterday")
                sys.exit(1)
    else:
        # Interactive mode
        print("\n" + "=" * 80)
        print("ACTIVITYWATCH WORKLOG ANALYZER (SQLite)")
        print("=" * 80)
        print("\nEnter date to analyze (formats: 2026-01-27, 27.01.2026, today, yesterday)")

        while True:
            date_input = input("\nDate: ").strip()
            if not date_input:
                print("No date entered. Exiting.")
                sys.exit(0)

            if date_input.lower() == 'today':
                target_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                break
            elif date_input.lower() == 'yesterday':
                target_date = (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                break
            else:
                target_date = parse_date(date_input)
                if target_date:
                    break
            print("Invalid date format. Please try again.")

    # Analyze
    print(f"Querying database for {target_date.strftime('%Y-%m-%d')}...")
    results = analyze_day(db_path, target_date)

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
