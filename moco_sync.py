#!/usr/bin/env python3
"""Idempotently synchronize an approved worklog into MOCO.

Existing activities are matched by date plus Jira remote ID (or tag fallback).
They are preserved by default. Passing --update-existing explicitly authorizes
repairing differences. Ticketed activities always receive native Jira links.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if sys.platform == 'win32':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')


def load_json(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        return json.load(handle)


def resolve_api_key():
    value = os.environ.get('MOCO_API_KEY')
    if value:
        return value
    if sys.platform == 'win32':
        try:
            import winreg
            locations = [
                (winreg.HKEY_CURRENT_USER, r'Environment'),
                (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'),
            ]
            for hive, subkey in locations:
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        value, _ = winreg.QueryValueEx(key, 'MOCO_API_KEY')
                    if value:
                        return value
                except OSError:
                    continue
        except ImportError:  # pragma: no cover
            pass
    raise RuntimeError('MOCO_API_KEY could not be resolved from the process, User, or Machine environment.')


def normalized_customer(value):
    return ' '.join(str(value or '').lower().split())


def resolve_project(config, customer, activity_date):
    moco = config.get('moco') or {}
    aliases = moco.get('customer_aliases') or {}
    normalized = normalized_customer(customer)
    canonical = next(
        (name for alias, name in aliases.items() if normalized_customer(alias) == normalized),
        customer,
    )
    year = str(datetime.strptime(activity_date, '%Y-%m-%d').year)
    mappings = ((moco.get('customer_projects') or {}).get(year) or {})
    for name, mapping in mappings.items():
        if normalized_customer(name) == normalized_customer(canonical):
            required = ('project_id', 'task_id')
            missing = [field for field in required if not mapping.get(field)]
            if missing:
                raise ValueError(f"MOCO mapping for {name} {year} is missing: {', '.join(missing)}")
            return name, mapping
    raise ValueError(f'No MOCO customer project mapping for {customer!r} in {year}.')


def seconds_from_activity(activity):
    if activity.get('seconds') is not None:
        seconds = int(activity['seconds'])
    elif activity.get('hours') is not None:
        seconds = round(float(activity['hours']) * 3600)
    else:
        raise ValueError(f"Activity {activity.get('ticket') or activity.get('description')} needs seconds or hours.")
    if seconds < 900:
        raise ValueError('MOCO activities must be at least 900 seconds (0.25h).')
    return seconds


def desired_payload(activity, default_date, config):
    activity_date = activity.get('date') or default_date
    datetime.strptime(activity_date, '%Y-%m-%d')
    ticket = str(activity.get('ticket') or '').strip().upper()
    sync_key = str(activity.get('sync_key') or '').strip()
    if not ticket and not sync_key:
        raise ValueError('A non-ticket activity needs a stable sync_key.')
    customer = activity.get('customer')
    activity_key = ticket or sync_key
    if not customer:
        raise ValueError(f'{activity_key} needs an explicit evidence-derived customer.')
    if 'billable' not in activity:
        raise ValueError(f'{activity_key} needs an explicit evidence-derived billable value.')
    customer_name, mapping = resolve_project(config, customer, activity_date)
    jira_base = ((config.get('moco') or {}).get('jira_base_url') or 'https://3volutions.atlassian.net/browse').rstrip('/')
    payload = {
        'date': activity_date,
        'project_id': int(mapping['project_id']),
        'task_id': int(mapping['task_id']),
        'seconds': seconds_from_activity(activity),
        'description': str(activity.get('description') or '').strip(),
        'billable': bool(activity['billable']),
        'tag': str(activity.get('tag') or activity_key),
        '_customer': customer_name,
        '_project_name': mapping.get('project_name'),
        '_task_name': mapping.get('task_name'),
    }
    if ticket:
        payload.update({
            'remote_service': 'jira',
            'remote_id': ticket,
            'remote_url': f'{jira_base}/{ticket}',
        })
    return payload


def public_payload(payload):
    return {
        key: value for key, value in payload.items()
        if not key.startswith('_') and value is not None
    }


class MocoClient:
    def __init__(self, base_url, api_key):
        self.base_url = base_url.rstrip('/') + '/api/v1'
        self.api_key = api_key

    def request(self, method, path, body=None, query=None):
        url = self.base_url + path
        if query:
            url += '?' + urlencode(query)
        data = None
        headers = {
            'Authorization': f'Token token={self.api_key}',
            'Accept': 'application/json',
        }
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:
                content = response.read().decode('utf-8')
                return json.loads(content) if content else None
        except HTTPError as error:
            detail = error.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'MOCO API {method} {path} failed with HTTP {error.code}: {detail}') from error

    def activities(self, activity_date):
        return self.request('GET', '/activities', query={'from': activity_date, 'to': activity_date}) or []

    def create_activity(self, payload):
        return self.request('POST', '/activities', public_payload(payload))

    def update_activity(self, activity_id, payload):
        return self.request('PUT', f'/activities/{activity_id}', public_payload(payload))


def existing_remote_id(activity):
    return str(activity.get('remote_id') or activity.get('remoteId') or '').upper()


def find_existing(activities, payload):
    ticket = str(payload.get('remote_id') or '').upper()
    tag = str(payload.get('tag') or '').upper()
    return next(
        (
            activity for activity in activities
            if activity.get('date') == payload['date']
            and (
                (ticket and existing_remote_id(activity) == ticket)
                or (
                    str(activity.get('tag') or '').upper() == tag
                    and int((activity.get('project') or {}).get('id') or activity.get('project_id') or 0)
                    == int(payload['project_id'])
                )
            )
        ),
        None,
    )


def comparable(activity):
    project = activity.get('project') or {}
    task = activity.get('task') or {}
    return {
        'project_id': int(project.get('id') or activity.get('project_id') or 0),
        'task_id': int(task.get('id') or activity.get('task_id') or 0),
        'seconds': int(activity.get('seconds') or activity.get('worked_seconds') or activity.get('workedSeconds') or 0),
        'description': activity.get('description') or '',
        'billable': bool(
            activity.get('billable')
            if 'billable' in activity
            else activity.get('is_billable')
            if 'is_billable' in activity
            else activity.get('isBillable')
        ),
        'tag': str(activity.get('tag') or ''),
        'remote_id': existing_remote_id(activity),
        'remote_url': activity.get('remote_url') or activity.get('remoteUrl') or '',
    }


def desired_comparable(payload):
    return {
        key: payload[key]
        for key in ('project_id', 'task_id', 'seconds', 'description', 'billable', 'tag', 'remote_id', 'remote_url')
        if key in payload
    }


def differences(existing, desired):
    current = comparable(existing)
    wanted = desired_comparable(desired)
    return {
        key: {'current': current[key], 'desired': wanted[key]}
        for key in wanted
        if current[key] != wanted[key]
    }


def synchronize(client, worklog, config, apply=False, update_existing=False):
    default_date = worklog.get('date')
    if not default_date:
        raise ValueError('The worklog needs a top-level date.')
    desired = [desired_payload(activity, default_date, config) for activity in worklog.get('activities') or []]
    if not desired:
        raise ValueError('The worklog contains no activities.')
    dates = sorted({item['date'] for item in desired})
    existing_by_date = {activity_date: client.activities(activity_date) for activity_date in dates}
    results = []

    for payload in desired:
        existing = find_existing(existing_by_date[payload['date']], payload)
        delta = differences(existing, payload) if existing else {}
        if existing and not delta:
            results.append({'action': 'unchanged', 'id': existing.get('id'), 'key': payload['tag']})
            continue
        if existing and not update_existing:
            results.append({
                'action': 'preserved-existing',
                'id': existing.get('id'),
                'key': payload['tag'],
                'differences': delta,
            })
            continue
        if not apply:
            results.append({
                'action': 'would-update' if existing else 'would-create',
                'id': existing.get('id') if existing else None,
                'key': payload['tag'],
                'project': payload.get('_project_name'),
                'task': payload.get('_task_name'),
                'seconds': payload['seconds'],
                'billable': payload['billable'],
                'differences': delta,
            })
            continue
        if existing:
            record = client.update_activity(existing['id'], payload)
            action = 'updated'
        else:
            record = client.create_activity(payload)
            action = 'created'
            existing_by_date[payload['date']].append(record)
        results.append({'action': action, 'id': record.get('id'), 'key': payload['tag']})

    verification = {}
    if apply:
        for activity_date in dates:
            verification[activity_date] = client.activities(activity_date)
        for payload in desired:
            stored = find_existing(verification[payload['date']], payload)
            if not stored:
                raise RuntimeError(f"Verification failed: {payload['tag']} is missing after synchronization.")
            delta = differences(stored, payload)
            matching_result = next(item for item in results if item['key'] == payload['tag'])
            if matching_result['action'] in ('created', 'updated', 'unchanged') and delta:
                raise RuntimeError(f"Verification failed for {payload['tag']}: {json.dumps(delta, ensure_ascii=False)}")

    return {'date': default_date, 'apply': apply, 'results': results}


def main():
    parser = argparse.ArgumentParser(description='Idempotently synchronize an approved JSON worklog to MOCO.')
    parser.add_argument('worklog', help='Approved worklog JSON file, or - to read JSON from stdin')
    parser.add_argument('--config', help='Configuration file (default: config.json)')
    parser.add_argument('--apply', action='store_true', help='Create missing activities (default is a dry run)')
    parser.add_argument(
        '--update-existing',
        action='store_true',
        help='Explicitly authorize replacing differing existing activity fields',
    )
    args = parser.parse_args()
    if args.update_existing and not args.apply:
        parser.error('--update-existing requires --apply')

    script_dir = Path(__file__).parent
    config = load_json(Path(args.config) if args.config else script_dir / 'config.json')
    worklog = json.load(sys.stdin) if args.worklog == '-' else load_json(args.worklog)
    api_key = resolve_api_key()
    base_url = ((config.get('moco') or {}).get('base_url') or 'https://3volutions.mocoapp.com')
    result = synchronize(MocoClient(base_url, api_key), worklog, config, args.apply, args.update_existing)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
