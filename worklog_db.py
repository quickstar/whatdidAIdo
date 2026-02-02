#!/usr/bin/env python3
"""
ActivityWatch Worklog Analyzer (SQLite version)
Queries ActivityWatch SQLite database directly for faster, real-time analysis.

Usage:
  python worklog_db.py                    Interactive mode
  python worklog_db.py 27.01.2026         Analyze specific date
  python worklog_db.py 27.01.2026 --ai    Compact output for AI interpretation
"""

import sqlite3
import json
import sys
import re
import argparse
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlparse
from pathlib import Path

# Configure UTF-8 output for Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Global config
CONFIG = {}


def get_db_path():
    """Get database path from config or environment."""
    # 1. Check environment variable
    import os
    if os.environ.get('AW_DATABASE'):
        return os.environ['AW_DATABASE']

    # 2. Check config.json
    if CONFIG.get('database'):
        return CONFIG['database']

    # 3. Fallback default
    return r"C:\Users\Lukas\OneDrive\ActivityWatchSync\andromeda\1750b5f1-5fee-4977-b0f5-4f433e976517\test.db"


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
        'total_active': 0,
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

            # Teams
            if 'ms-teams' in app.lower():
                convo = title.split('|')[0].strip() if '|' in title else title
                results['teams'][clean(convo[:50])] += duration

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

    # AFK status
    afk_bucket = buckets.get('aw-watcher-afk_andromeda')
    if afk_bucket:
        events = query_events(cursor, afk_bucket['id'], start_ns, end_ns)
        for start, end, data_json in events:
            duration = (end - start) / 1_000_000_000
            data = json.loads(data_json)
            status = data.get('status', '')
            if status == 'not-afk':
                results['total_active'] += duration
                if duration >= 300:
                    ts = datetime.fromtimestamp(start / 1_000_000_000)
                    results['active_periods'].append((ts, duration))

    results['active_periods'].sort()
    conn.close()
    return results


def detect_clients(results):
    """Detect which clients were worked on based on domains."""
    clients = CONFIG.get('clients', {})
    detected = defaultdict(float)
    for domain, dur in results['domain_time'].items():
        for key, name in clients.items():
            if key in domain.lower():
                detected[name] += dur
    return detected


def print_ai_summary(results, target_date):
    """Print compact AI-friendly summary for interpretation."""
    total_hours = results['total_active'] / 3600
    date_str = target_date.strftime('%Y-%m-%d')

    print(f"# ActivityWatch Data for {date_str}")
    print(f"**Total Active: {total_hours:.1f}h**")

    if results['active_periods']:
        first = results['active_periods'][0][0].strftime('%H:%M')
        last = results['active_periods'][-1][0].strftime('%H:%M')
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
    print(f"\nTotal Active Time: {total_hours:.1f} hours")

    if results['active_periods']:
        first = results['active_periods'][0][0].strftime('%H:%M')
        last = results['active_periods'][-1][0].strftime('%H:%M')
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
    print(f"\n  Total billable estimate: {total_hours * 0.85:.1f}h (85% of active time)")
    print(f"\n  Tip: Run with --ai flag for compact summary to paste to Claude")
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Analyze ActivityWatch SQLite database for worklog generation.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python worklog_db.py                     Interactive mode
  python worklog_db.py 27.01.2026          Analyze specific date
  python worklog_db.py 27.01.2026 --ai     Compact output for AI interpretation
  python worklog_db.py today               Analyze today
  python worklog_db.py yesterday           Analyze yesterday
        """
    )
    parser.add_argument('date', nargs='?', help='Date to analyze (e.g., 27.01.2026, today, yesterday)')
    parser.add_argument('--ai', action='store_true', help='Output compact format for AI interpretation')
    parser.add_argument('--db', help='Path to SQLite database (default: from config.json or AW_DATABASE env)')
    parser.add_argument('--config', help='Path to config.json')

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

    if results['total_active'] == 0:
        print(f"\nNo activity found for {target_date.strftime('%Y-%m-%d')}")
        sys.exit(1)

    if args.ai:
        print_ai_summary(results, target_date)
    else:
        print_summary(results, target_date)


if __name__ == '__main__':
    main()
