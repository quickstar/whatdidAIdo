#!/usr/bin/env python3
"""Audit GitHub and local git activity for one Europe/Zurich workday.

The JSON output retains commit metadata and patches for later interpretation.
The --ai output is a compact inventory suitable for a worklog agent.
"""

import argparse
import calendar
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python 3.9+ is expected
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception


def parse_date(value):
    value = value.strip().lower()
    today = datetime.now().date()
    if value == 'today':
        return today
    if value == 'yesterday':
        return today - timedelta(days=1)
    for pattern in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported date: {value}")


def _last_sunday(year, month):
    last_day = calendar.monthrange(year, month)[1]
    candidate = date(year, month, last_day)
    return candidate - timedelta(days=(candidate.weekday() + 1) % 7)


def _zurich_fallback_offset(local_date):
    """Return the UTC offset at local midnight using the EU DST rules."""
    spring = _last_sunday(local_date.year, 3)
    autumn = _last_sunday(local_date.year, 10)
    if spring < local_date <= autumn:
        return timedelta(hours=2)
    return timedelta(hours=1)


def local_day_utc_bounds(target_date, timezone_name='Europe/Zurich'):
    """Return exact UTC bounds for a local calendar day, including DST days."""
    if ZoneInfo is not None:
        try:
            zone = ZoneInfo(timezone_name)
            local_start = datetime.combine(target_date, time.min, zone)
            local_end = datetime.combine(target_date + timedelta(days=1), time.min, zone)
            return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)
        except ZoneInfoNotFoundError:
            if timezone_name != 'Europe/Zurich':
                raise

    start_offset = _zurich_fallback_offset(target_date)
    end_offset = _zurich_fallback_offset(target_date + timedelta(days=1))
    local_start = datetime.combine(target_date, time.min).replace(
        tzinfo=timezone(start_offset)
    )
    local_end = datetime.combine(target_date + timedelta(days=1), time.min).replace(
        tzinfo=timezone(end_offset)
    )
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def parse_timestamp(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(timezone.utc)


def timestamp_in_bounds(value, start, end):
    parsed = parse_timestamp(value)
    return parsed is not None and start <= parsed < end


def decode_json_stream(text):
    """Decode gh --paginate output, which may contain adjacent JSON documents."""
    decoder = json.JSONDecoder()
    values = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        value, index = decoder.raw_decode(text, index)
        values.append(value)
    return values


def flatten_pages(values, item_key=None):
    result = []
    for value in values:
        if item_key and isinstance(value, dict):
            result.extend(value.get(item_key) or [])
        elif isinstance(value, list):
            result.extend(value)
        else:
            result.append(value)
    return result


def run_command(arguments, cwd=None, check=True):
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        text=True,
        encoding='utf-8',
        errors='replace',
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Command failed ({' '.join(arguments)}): {detail}")
    return completed


def run_git(arguments, repository_path, check=True):
    """Run git without changing global safe.directory state in a sandbox."""
    return run_command(
        ['git', '-c', f'safe.directory={Path(repository_path).as_posix()}', *arguments],
        cwd=repository_path,
        check=check,
    )


def gh_json(path, method='GET', fields=None, paginate=False):
    arguments = ['gh', 'api']
    if method != 'GET' or fields:
        arguments.extend(['--method', method])
    arguments.append(path)
    for key, value in (fields or {}).items():
        arguments.extend(['-f', f'{key}={value}'])
    if paginate:
        arguments.append('--paginate')
    output = run_command(arguments).stdout
    values = decode_json_stream(output)
    return values if paginate else (values[0] if values else None)


def repo_from_api_url(value):
    match = re.search(r'/repos/([^/]+/[^/]+)', value or '')
    return match.group(1) if match else None


def repo_from_remote(value):
    value = (value or '').strip()
    match = re.search(r'github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$', value)
    return match.group(1) if match else None


def commit_times(commit):
    details = commit.get('commit') or {}
    return (
        ((details.get('author') or {}).get('date')),
        ((details.get('committer') or {}).get('date')),
    )


def commit_is_in_day(commit, start, end):
    return any(timestamp_in_bounds(value, start, end) for value in commit_times(commit))


class GitHubAudit:
    def __init__(self, target_date, config, login=None):
        github_config = config.get('github') or {}
        self.target_date = target_date
        self.timezone_name = github_config.get('timezone', 'Europe/Zurich')
        self.start, self.end = local_day_utc_bounds(target_date, self.timezone_name)
        self.login = login or github_config.get('login')
        self.local_roots = [Path(os.path.expandvars(path)) for path in github_config.get('local_repositories', [])]
        self.repositories_root = github_config.get('repositories_root')
        self.git_authors = github_config.get('git_authors') or ['Lukas']
        self.repositories = set()
        self.commits = {}
        self.pull_requests = {}
        self.events = []
        self.pushes = []
        self.rewrite_edges = []
        self.warnings = []

    def add_commit(self, repository, value, evidence):
        if not repository or not value:
            return None
        sha = value.get('sha') or value.get('oid')
        if not sha:
            return None
        key = f'{repository}@{sha}'
        record = self.commits.setdefault(key, {
            'repository': repository,
            'sha': sha,
            'evidence': [],
            'superseded_by': None,
        })
        if evidence not in record['evidence']:
            record['evidence'].append(evidence)
        if value.get('commit') and not record.get('commit'):
            record.update(value)
            record['repository'] = repository
            record['evidence'] = record.get('evidence', [])
            record.setdefault('superseded_by', None)
        self.repositories.add(repository)
        return record

    def resolve_login(self):
        if not self.login:
            self.login = (gh_json('user') or {}).get('login')
        if not self.login:
            raise RuntimeError('Could not resolve the authenticated GitHub login.')

    def search_dates(self):
        return [self.target_date - timedelta(days=1), self.target_date, self.target_date + timedelta(days=1)]

    def discover_commit_searches(self):
        for search_date in self.search_dates():
            for qualifier in ('author', 'committer'):
                query = f'{qualifier}:{self.login} {qualifier}-date:{search_date.isoformat()}'
                try:
                    pages = gh_json('/search/commits', fields={'q': query, 'per_page': '100'}, paginate=True)
                    for item in flatten_pages(pages, 'items'):
                        repository = ((item.get('repository') or {}).get('full_name'))
                        if commit_is_in_day(item, self.start, self.end):
                            self.add_commit(repository, item, f'commit-search:{qualifier}')
                except RuntimeError as error:
                    self.warnings.append(str(error))

    def discover_events(self):
        try:
            pages = gh_json(f'users/{self.login}/events?per_page=100', paginate=True)
            events = flatten_pages(pages)
        except RuntimeError as error:
            self.warnings.append(str(error))
            return

        if self.target_date < datetime.now().date() - timedelta(days=90):
            self.warnings.append(
                'GitHub user events have limited retention and may not cover this date; '
                'commit, PR, and local-git discovery still ran.'
            )

        for event in events:
            if not timestamp_in_bounds(event.get('created_at'), self.start, self.end):
                continue
            repository = ((event.get('repo') or {}).get('name'))
            if repository:
                self.repositories.add(repository)
            compact = {
                'id': event.get('id'),
                'type': event.get('type'),
                'created_at': event.get('created_at'),
                'repository': repository,
                'action': ((event.get('payload') or {}).get('action')),
            }
            self.events.append(compact)
            payload = event.get('payload') or {}
            if event.get('type') == 'PushEvent':
                push = {
                    'repository': repository,
                    'created_at': event.get('created_at'),
                    'ref': payload.get('ref'),
                    'before': payload.get('before'),
                    'head': payload.get('head'),
                }
                self.pushes.append(push)
            pull_request = payload.get('pull_request') or {}
            issue = payload.get('issue') or {}
            number = pull_request.get('number')
            if not number and issue.get('pull_request'):
                number = issue.get('number')
            if repository and number:
                self.pull_requests[f'{repository}#{number}'] = {
                    'repository': repository,
                    'number': number,
                    'evidence': ['event'],
                }

    def discover_pull_requests(self):
        for search_date in self.search_dates():
            query = f'is:pr involves:{self.login} updated:{search_date.isoformat()}'
            try:
                pages = gh_json('/search/issues', fields={'q': query, 'per_page': '100'}, paginate=True)
            except RuntimeError as error:
                self.warnings.append(str(error))
                continue
            for item in flatten_pages(pages, 'items'):
                repository = repo_from_api_url(item.get('repository_url'))
                number = item.get('number')
                if not repository or not number:
                    continue
                key = f'{repository}#{number}'
                if key not in self.pull_requests and not timestamp_in_bounds(
                    item.get('updated_at'), self.start, self.end
                ):
                    continue
                record = self.pull_requests.setdefault(key, {
                    'repository': repository,
                    'number': number,
                    'evidence': [],
                })
                if 'search' not in record['evidence']:
                    record['evidence'].append('search')
                record.update({
                    'title': item.get('title'),
                    'state': item.get('state'),
                    'updated_at': item.get('updated_at'),
                    'html_url': item.get('html_url'),
                })
                self.repositories.add(repository)

        for record in list(self.pull_requests.values()):
            repository = record['repository']
            number = record['number']
            try:
                details = gh_json(f'repos/{repository}/pulls/{number}') or {}
                record.update({
                    'title': details.get('title') or record.get('title'),
                    'state': details.get('state') or record.get('state'),
                    'merged_at': details.get('merged_at'),
                    'created_at': details.get('created_at'),
                    'updated_at': details.get('updated_at') or record.get('updated_at'),
                    'html_url': details.get('html_url') or record.get('html_url'),
                    'head_sha': ((details.get('head') or {}).get('sha')),
                    'base_sha': ((details.get('base') or {}).get('sha')),
                    'body': details.get('body') or '',
                })
                issue_comments = gh_json(
                    f'repos/{repository}/issues/{number}/comments?per_page=100',
                    paginate=True,
                )
                reviews = gh_json(
                    f'repos/{repository}/pulls/{number}/reviews?per_page=100',
                    paginate=True,
                )
                review_comments = gh_json(
                    f'repos/{repository}/pulls/{number}/comments?per_page=100',
                    paginate=True,
                )
                record['issue_comments'] = [
                    item for item in flatten_pages(issue_comments)
                    if timestamp_in_bounds(item.get('created_at'), self.start, self.end)
                    or timestamp_in_bounds(item.get('updated_at'), self.start, self.end)
                ]
                record['reviews'] = [
                    item for item in flatten_pages(reviews)
                    if timestamp_in_bounds(item.get('submitted_at'), self.start, self.end)
                ]
                record['review_comments'] = [
                    item for item in flatten_pages(review_comments)
                    if timestamp_in_bounds(item.get('created_at'), self.start, self.end)
                    or timestamp_in_bounds(item.get('updated_at'), self.start, self.end)
                ]
                pages = gh_json(f'repos/{repository}/pulls/{number}/commits?per_page=100', paginate=True)
                for commit in flatten_pages(pages):
                    _, committed = commit_times(commit)
                    if timestamp_in_bounds(committed, self.start, self.end):
                        self.add_commit(repository, commit, f'pull-request:{number}')
            except RuntimeError as error:
                self.warnings.append(str(error))

    def inspect_pushes(self):
        for push in sorted(self.pushes, key=lambda item: item.get('created_at') or ''):
            repository = push.get('repository')
            before = push.get('before')
            head = push.get('head')
            if not repository or not before or not head or set(before) == {'0'}:
                continue
            try:
                comparison = gh_json(f'repos/{repository}/compare/{before}...{head}') or {}
                push.update({
                    'status': comparison.get('status'),
                    'ahead_by': comparison.get('ahead_by'),
                    'behind_by': comparison.get('behind_by'),
                })
                for commit in comparison.get('commits') or []:
                    self.add_commit(repository, commit, f'push:{push.get("ref")}')
                self.add_commit(repository, {'sha': head}, f'push-head:{push.get("ref")}')
                if comparison.get('status') == 'diverged':
                    edge = {'repository': repository, 'old': before, 'new': head, 'ref': push.get('ref')}
                    self.rewrite_edges.append(edge)
                    old_key = f'{repository}@{before}'
                    if old_key in self.commits:
                        self.commits[old_key]['superseded_by'] = head
            except RuntimeError as error:
                self.warnings.append(str(error))

    def configured_local_repositories(self):
        candidates = list(self.local_roots)
        if self.repositories_root:
            root = Path(os.path.expandvars(self.repositories_root))
            if root.is_dir():
                candidates.extend(path for path in root.iterdir() if path.is_dir() and (path / '.git').exists())
        unique = []
        seen = set()
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if resolved not in seen and (resolved / '.git').exists():
                unique.append(resolved)
                seen.add(resolved)
        return unique

    def inspect_local_repositories(self):
        since = self.start.isoformat()
        until = self.end.isoformat()
        for repository_path in self.configured_local_repositories():
            remote = run_git(['remote', 'get-url', 'origin'], repository_path, check=False).stdout.strip()
            repository = repo_from_remote(remote) or repository_path.name
            should_scan = repository in self.repositories
            if not should_scan:
                # Local-only work may be the only evidence for a repository.
                should_scan = True
            if not should_scan:
                continue
            arguments = [
                'log', '--all', f'--since={since}', f'--until={until}',
                '--format=%H%x1f%aI%x1f%cI%x1f%an%x1f%ae%x1f%s%x1e',
            ]
            completed = run_git(arguments, repository_path, check=False)
            if completed.returncode != 0:
                self.warnings.append(f'Local git log failed for {repository_path}: {completed.stderr.strip()}')
                continue
            for raw in completed.stdout.split('\x1e'):
                fields = raw.strip().split('\x1f')
                if len(fields) != 6:
                    continue
                sha, authored, committed, author_name, author_email, subject = fields
                if self.git_authors and not any(
                    author.lower() in f'{author_name} {author_email}'.lower()
                    for author in self.git_authors
                ):
                    continue
                record = self.add_commit(repository, {
                    'sha': sha,
                    'commit': {
                        'author': {'name': author_name, 'email': author_email, 'date': authored},
                        'committer': {'date': committed},
                        'message': subject,
                    },
                }, f'local:{repository_path}')
                if record is not None:
                    record['local_path'] = str(repository_path)
                    record['github_visible'] = record.get('github_visible', False)

    def hydrate_commits(self):
        for record in list(self.commits.values()):
            repository = record['repository']
            sha = record['sha']
            try:
                details = gh_json(f'repos/{repository}/commits/{sha}') or {}
                evidence = record['evidence']
                superseded_by = record.get('superseded_by')
                local_path = record.get('local_path')
                record.clear()
                record.update(details)
                record.update({
                    'repository': repository,
                    'sha': sha,
                    'evidence': evidence,
                    'superseded_by': superseded_by,
                    'local_path': local_path,
                    'github_visible': True,
                })
                authored, committed = commit_times(record)
                record['authored_in_day'] = timestamp_in_bounds(authored, self.start, self.end)
                record['committed_in_day'] = timestamp_in_bounds(committed, self.start, self.end)
                pulls = gh_json(f'repos/{repository}/commits/{sha}/pulls') or []
                record['pull_requests'] = [
                    {'number': item.get('number'), 'title': item.get('title'), 'html_url': item.get('html_url')}
                    for item in pulls
                ]
                if local_path:
                    self.hydrate_local_commit(record)
            except RuntimeError:
                record['github_visible'] = False
                if record.get('local_path'):
                    self.hydrate_local_commit(record)
                else:
                    self.warnings.append(f'Could not inspect {repository}@{sha}.')

    def hydrate_local_commit(self, record):
        path = Path(record['local_path'])
        sha = record['sha']
        completed = run_git(
            ['show', '--format=%B', '--numstat', '--no-renames', sha],
            path,
            check=False,
        )
        if completed.returncode != 0:
            return
        lines = completed.stdout.splitlines()
        files = []
        for line in lines:
            match = re.match(r'^(\d+|-)\t(\d+|-)\t(.+)$', line)
            if match:
                additions = None if match.group(1) == '-' else int(match.group(1))
                deletions = None if match.group(2) == '-' else int(match.group(2))
                files.append({'filename': match.group(3), 'additions': additions, 'deletions': deletions})
        patch = run_git(['show', '--format=', '--no-renames', sha], path, check=False).stdout
        record['files'] = files
        record['local_patch'] = patch
        record['stats'] = {
            'additions': sum(item['additions'] or 0 for item in files),
            'deletions': sum(item['deletions'] or 0 for item in files),
            'total': sum((item['additions'] or 0) + (item['deletions'] or 0) for item in files),
        }

    def run(self):
        self.resolve_login()
        self.discover_commit_searches()
        self.discover_events()
        self.discover_pull_requests()
        self.inspect_pushes()
        self.inspect_local_repositories()
        self.hydrate_commits()
        return self.result()

    def result(self):
        commits = sorted(
            self.commits.values(),
            key=lambda item: (
                item.get('repository') or '',
                ((item.get('commit') or {}).get('author') or {}).get('date') or '',
                item.get('sha') or '',
            ),
        )
        return {
            'date': self.target_date.isoformat(),
            'timezone': self.timezone_name,
            'utc_start': self.start.isoformat(),
            'utc_end': self.end.isoformat(),
            'login': self.login,
            'repositories': sorted(self.repositories | {item['repository'] for item in commits}),
            'commits': commits,
            'pull_requests': sorted(self.pull_requests.values(), key=lambda item: (item['repository'], item['number'])),
            'events': sorted(self.events, key=lambda item: item.get('created_at') or ''),
            'pushes': sorted(self.pushes, key=lambda item: item.get('created_at') or ''),
            'rewrite_edges': self.rewrite_edges,
            'warnings': self.warnings,
        }


def first_line(value):
    return (value or '').splitlines()[0] if value else ''


def print_ai(result):
    print(f"# GitHub Audit for {result['date']} ({result['timezone']})")
    print(f"Login: {result['login']} | Repositories: {len(result['repositories'])} | Distinct SHAs: {len(result['commits'])}")
    print(f"UTC bounds: {result['utc_start']} - {result['utc_end']}")
    for repository in result['repositories']:
        print(f"\n## {repository}")
        commits = [item for item in result['commits'] if item['repository'] == repository]
        for item in commits:
            details = item.get('commit') or {}
            authored = ((details.get('author') or {}).get('date')) or '-'
            stats = item.get('stats') or {}
            files = item.get('files') or []
            state = 'superseded' if item.get('superseded_by') else ('GitHub' if item.get('github_visible') else 'local-only')
            print(
                f"- {item['sha'][:10]} | {authored} | {state} | "
                f"+{stats.get('additions', 0)}/-{stats.get('deletions', 0)} | "
                f"{first_line(details.get('message'))}"
            )
            if files:
                print(f"  Files ({len(files)}): {', '.join(file['filename'] for file in files[:12])}")
            if item.get('pull_requests'):
                print('  PRs: ' + ', '.join(f"#{pr['number']} {pr['title']}" for pr in item['pull_requests']))
            print('  Evidence: ' + ', '.join(item.get('evidence') or []))
    if result['pull_requests']:
        print("\n## Pull Requests")
        for pull in result['pull_requests']:
            state = 'merged' if pull.get('merged_at') else (pull.get('state') or '-')
            print(f"- {pull['repository']}#{pull['number']}: {pull.get('title') or '-'} [{state}]")
            def authored_by_login(items):
                return sum(
                    1 for item in items or []
                    if ((item.get('user') or {}).get('login') or '').lower() == result['login'].lower()
                )
            activity_counts = {
                'issue comments': authored_by_login(pull.get('issue_comments')),
                'reviews': authored_by_login(pull.get('reviews')),
                'inline comments': authored_by_login(pull.get('review_comments')),
            }
            visible_counts = [f'{count} {label}' for label, count in activity_counts.items() if count]
            if visible_counts:
                print('  User day activity: ' + ', '.join(visible_counts))
    if result['rewrite_edges']:
        print("\n## Rewrites")
        for edge in result['rewrite_edges']:
            print(f"- {edge['repository']} {edge.get('ref')}: {edge['old'][:10]} -> {edge['new'][:10]}")
    if result['warnings']:
        print("\n## Coverage Warnings")
        for warning in result['warnings']:
            print(f"- {warning}")


def load_config(path):
    if not path.exists():
        return {}
    with path.open('r', encoding='utf-8') as handle:
        return json.load(handle)


def main():
    parser = argparse.ArgumentParser(description='Audit GitHub and local git work for one local date.')
    parser.add_argument('date', help='Date such as 2026-07-28, 28.07.2026, today, or yesterday')
    parser.add_argument('--config', help='Configuration file (default: config.json)')
    parser.add_argument('--login', help='GitHub login override')
    parser.add_argument('--ai', action='store_true', help='Print compact agent-oriented output')
    parser.add_argument('--output', help='Write full JSON evidence to this file')
    args = parser.parse_args()

    try:
        target_date = parse_date(args.date)
    except ValueError as error:
        parser.error(str(error))
    script_dir = Path(__file__).parent
    config = load_config(Path(args.config) if args.config else script_dir / 'config.json')
    result = GitHubAudit(target_date, config, args.login).run()
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + '\n', encoding='utf-8')
    if args.ai:
        print_ai(result)
    elif not args.output:
        print(rendered)


if __name__ == '__main__':
    main()
