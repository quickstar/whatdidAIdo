#!/usr/bin/env python3
"""
ActivityWatch Worklog Analyzer
Analyzes ActivityWatch export data and generates worklog summaries.

Usage:
  python worklog.py           Interactive mode - asks for date
  python worklog.py 27.01.2026    Analyze specific date
  python worklog.py 27.01.2026 --ai   Output compact format for AI interpretation
"""

import json
import sys
import re
import argparse
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse
from pathlib import Path

# Configure UTF-8 output for Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Global config
CONFIG = {}

def load_config(script_dir):
    """Load configuration from config.json if available."""
    global CONFIG
    config_file = script_dir / 'config.json'
    if config_file.exists():
        with open(config_file, 'r', encoding='utf-8') as f:
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

def load_data(filepath):
    """Load the ActivityWatch export JSON."""
    print(f"Loading data from {filepath}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def analyze_day(data, target_date):
    """Analyze all activity for a given date."""
    results = {
        'app_time': defaultdict(float),
        'window_details': defaultdict(lambda: defaultdict(float)),
        'jira_tickets': defaultdict(float),
        'domain_time': defaultdict(float),
        'file_time': defaultdict(float),
        'branches': defaultdict(float),
        'teams': defaultdict(float),
        'active_periods': [],
        'total_active': 0,
    }

    # Window activity
    window_bucket = data['buckets'].get('aw-watcher-window_andromeda', {})
    for event in window_bucket.get('events', []):
        if not event.get('timestamp', '').startswith(target_date):
            continue
        duration = event.get('duration', 0)
        app = event.get('data', {}).get('app', 'Unknown')
        title = event.get('data', {}).get('title', '')

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

    # Web activity
    for bucket_name in ['aw-watcher-web-edge_andromeda', 'aw-watcher-web-firefox_andromeda', 'aw-watcher-web-firefox']:
        bucket = data['buckets'].get(bucket_name, {})
        for event in bucket.get('events', []):
            if not event.get('timestamp', '').startswith(target_date):
                continue
            duration = event.get('duration', 0)
            url = event.get('data', {}).get('url', '')
            title = event.get('data', {}).get('title', '')

            if url:
                domain = urlparse(url).netloc
                results['domain_time'][domain] += duration

            # JIRA tickets
            matches = re.findall(r'ROMSD-\d+', title + url)
            for m in matches:
                results['jira_tickets'][m] += duration

    # IDE files
    for bucket_name in ['aw-watcher-jetbrains-rider_andromeda', 'aw-watcher-vscode_andromeda']:
        bucket = data['buckets'].get(bucket_name, {})
        for event in bucket.get('events', []):
            if not event.get('timestamp', '').startswith(target_date):
                continue
            duration = event.get('duration', 0)
            file = event.get('data', {}).get('file', '')
            if file:
                results['file_time'][file] += duration

    # AFK status (active periods)
    afk_bucket = data['buckets'].get('aw-watcher-afk_andromeda', {})
    for event in afk_bucket.get('events', []):
        if not event.get('timestamp', '').startswith(target_date):
            continue
        status = event.get('data', {}).get('status', '')
        if status == 'not-afk':
            dur = event.get('duration', 0)
            results['total_active'] += dur
            if dur >= 300:
                ts = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
                results['active_periods'].append((ts, dur))

    results['active_periods'].sort()
    return results

def print_summary(results, target_date):
    """Print formatted worklog summary."""

    print("\n" + "=" * 80)
    print(f"WORKLOG SUMMARY - {target_date}")
    print("=" * 80)

    # Active time
    total_hours = results['total_active'] / 3600
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

    # IDE files
    if results['file_time']:
        print("\n" + "-" * 80)
        print("FILES WORKED ON")
        print("-" * 80)
        for f, dur in sorted(results['file_time'].items(), key=lambda x: -x[1])[:10]:
            if dur >= 60:
                print(f"  {format_duration(dur):>6}  {clean(f)}")

    # Git branches
    if results['branches']:
        print("\n" + "-" * 80)
        print("GIT BRANCHES")
        print("-" * 80)
        for branch, dur in sorted(results['branches'].items(), key=lambda x: -x[1]):
            if dur >= 30:
                branch_short = branch[:60] + "..." if len(branch) > 60 else branch
                print(f"  {format_duration(dur):>6}  {branch_short}")

    # Web domains
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

    # Suggested worklog
    print("\n" + "=" * 80)
    print("SUGGESTED WORKLOG ENTRIES")
    print("=" * 80)
    print("\n  Copy the above data to fill in your worklog.")
    print(f"  Total billable estimate: {total_hours * 0.85:.1f}h (85% of active time)")
    print()
    print("  Tip: Run with --ai flag for a compact summary to paste to Claude:")
    print(f"       python worklog.py {target_date} --ai")
    print()


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

    print(f"# ActivityWatch Data for {target_date}")
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
                # Add type hint if known
                hint = ""
                for prefix, desc in prefixes.items():
                    if ticket.startswith(prefix):
                        hint = f" ({desc})"
                        break
                # Check for known ticket description
                known = CONFIG.get('known_tickets', {}).get(ticket, "")
                if known:
                    hint = f" - {known}"
                print(f"- {ticket}: {format_duration(dur)}{hint}")

    # Window context (most important for understanding work)
    print("\n## Window Titles (context)")
    for app in sorted(results['app_time'].keys(), key=lambda x: -results['app_time'][x])[:8]:
        titles = results['window_details'][app]
        relevant_titles = [(t, d) for t, d in titles.items() if d >= 60]
        if relevant_titles and results['app_time'][app] >= 180:
            print(f"\n**{app}**")
            for title, dur in sorted(relevant_titles, key=lambda x: -x[1])[:4]:
                print(f"- [{format_duration(dur)}] {title[:80]}")

    # Files with project hints
    if results['file_time']:
        files_over_1m = [(f, d) for f, d in results['file_time'].items() if d >= 60]
        if files_over_1m:
            print("\n## Files Edited")
            projects = CONFIG.get('projects', {})
            for f, dur in sorted(files_over_1m, key=lambda x: -x[1])[:12]:
                # Try to identify project
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
                # Check for environment
                for env_domain, env_name in environments.items():
                    if env_domain in domain:
                        hint = f" [{env_name}]"
                        break
                # Check for context hints
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

    # Likely personal activities (for awareness, not filtering)
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
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue

    return None

def main():
    parser = argparse.ArgumentParser(
        description='Analyze ActivityWatch export data for worklog generation.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python worklog.py                    Interactive mode
  python worklog.py 27.01.2026         Analyze specific date
  python worklog.py 27.01.2026 --ai    Compact output for AI interpretation
  python worklog.py 2026-01-27 --ai    ISO date format also works
        """
    )
    parser.add_argument('date', nargs='?', help='Date to analyze (e.g., 27.01.2026 or 2026-01-27)')
    parser.add_argument('--ai', action='store_true', help='Output compact format for AI interpretation')
    parser.add_argument('--file', '-f', help='Path to aw-buckets-export.json (default: same directory)')

    args = parser.parse_args()

    # Find the export file and load config
    script_dir = Path(__file__).parent
    load_config(script_dir)

    if args.file:
        export_file = Path(args.file)
    else:
        export_file = script_dir / 'aw-buckets-export.json'

    if not export_file.exists():
        print(f"Error: Could not find {export_file}")
        print("Make sure aw-buckets-export.json is in the same directory as this script.")
        sys.exit(1)

    # Get date
    if args.date:
        target_date = parse_date(args.date)
        if not target_date:
            print(f"Error: Invalid date format '{args.date}'")
            print("Use formats like: 2026-01-27, 27.01.2026, 27/01/2026")
            sys.exit(1)
    else:
        # Interactive mode
        print("\n" + "=" * 80)
        print("ACTIVITYWATCH WORKLOG ANALYZER")
        print("=" * 80)
        print("\nEnter date to analyze (formats: 2026-01-27, 27.01.2026, 27/01/2026)")

        while True:
            date_input = input("\nDate: ").strip()
            if not date_input:
                print("No date entered. Exiting.")
                sys.exit(0)

            target_date = parse_date(date_input)
            if target_date:
                break
            print("Invalid date format. Please try again.")

    # Load and analyze
    data = load_data(export_file)
    results = analyze_day(data, target_date)

    if results['total_active'] == 0:
        print(f"\nNo activity found for {target_date}")
        print("Make sure the date is correct and within the export range.")
        sys.exit(1)

    if args.ai:
        print_ai_summary(results, target_date)
    else:
        print_summary(results, target_date)


if __name__ == '__main__':
    main()
