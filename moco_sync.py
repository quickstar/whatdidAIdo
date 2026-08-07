#!/usr/bin/env python3
"""Idempotently synchronize an approved worklog into MOCO.

Existing Jira activities are matched by date plus remote ID. Untagged non-Jira
activities use a local sync-key to MOCO-ID ledger with legacy natural-identity
fallbacks. Activities are preserved by default. Passing --update-existing
explicitly authorizes repairing differences. Ticketed activities always receive
native Jira links.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
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


def load_identity_state(path):
    state_path = Path(path)
    if not state_path.exists():
        return {'version': 1, 'activities': {}}
    state = load_json(state_path)
    if not isinstance(state, dict) or not isinstance(state.get('activities'), dict):
        raise ValueError(f'Invalid MOCO sync state file: {state_path}')
    state.setdefault('version', 1)
    return state


def save_identity_state(path, state):
    state_path = Path(path)
    temporary = state_path.with_name(state_path.name + '.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(state_path)


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


def approved_total_seconds(worklog, desired, warnings):
    desired_seconds = sum(item['seconds'] for item in desired)
    hours = worklog.get('approved_total_hours')
    seconds = worklog.get('approved_total_seconds')
    if hours is None and seconds is None:
        warnings.append('Top-level approved_total_hours is missing; the desired sum was not independently asserted.')
        return None
    approved = round(float(hours) * 3600) if hours is not None else int(seconds)
    if hours is not None and seconds is not None and approved != int(seconds):
        raise ValueError('approved_total_hours and approved_total_seconds disagree.')
    if approved != desired_seconds:
        raise ValueError(
            f'Approved total is {approved} seconds but activities sum to {desired_seconds} seconds.'
        )
    return approved


def canonical_customer(config, customer):
    aliases = ((config.get('moco') or {}).get('customer_aliases') or {})
    normalized = normalized_customer(customer)
    return next(
        (name for alias, name in aliases.items() if normalized_customer(alias) == normalized),
        customer,
    )


def semantic_warnings(worklog, config):
    """Return non-blocking warnings for attribution that cannot be proven structurally."""
    warnings = []
    aliases = ((config.get('moco') or {}).get('customer_aliases') or {})
    known_names = set(aliases) | set(aliases.values())
    for activity in worklog.get('activities') or []:
        key = str(activity.get('ticket') or activity.get('sync_key') or activity.get('description') or '?')
        description = str(activity.get('description') or '')
        ticket = str(activity.get('ticket') or '').upper()
        referenced = set(re.findall(r'\b[A-Z][A-Z0-9]+-\d+\b', description.upper()))
        extra_tickets = sorted(referenced - ({ticket} if ticket else set()))
        if extra_tickets:
            warnings.append(f"{key}: description mentions additional ticket(s): {', '.join(extra_tickets)}.")

        assigned = normalized_customer(canonical_customer(config, activity.get('customer')))
        foreign_customers = sorted({
            str(canonical_customer(config, name))
            for name in known_names
            if len(str(name).strip()) >= 3
            and normalized_customer(canonical_customer(config, name)) != assigned
            and normalized_customer(name) in normalized_customer(description)
        })
        if foreign_customers:
            warnings.append(f"{key}: description may mix assigned customer with {', '.join(foreign_customers)}.")

        if (
            not ticket
            and assigned
            and not assigned.startswith('internal')
            and not str(activity.get('billability_evidence') or '').strip()
        ):
            warnings.append(
                f'{key}: customer-specific non-Jira work has no billability_evidence.'
            )
    return warnings


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
        'tag': str(activity.get('tag') or ticket) if ticket else '',
        '_activity_key': activity_key,
        '_sync_key': sync_key,
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


def identity_state_key(payload):
    sync_key = str(payload.get('_sync_key') or '').strip()
    return f"{payload['date']}|{sync_key}" if sync_key else ''


def remember_identity(identity_state, payload, activity):
    state_key = identity_state_key(payload)
    activity_id = activity.get('id') if activity else None
    if state_key and activity_id is not None:
        identity_state.setdefault('activities', {})[state_key] = str(activity_id)


def append_warning(warnings, warning):
    if warnings is not None and warning not in warnings:
        warnings.append(warning)


def find_existing(activities, payload, identity_state=None, warnings=None):
    ticket = str(payload.get('remote_id') or '').upper()
    sync_key = str(payload.get('_sync_key') or '').upper()

    def project_id(activity):
        return int((activity.get('project') or {}).get('id') or activity.get('project_id') or 0)

    def task_id(activity):
        return int((activity.get('task') or {}).get('id') or activity.get('task_id') or 0)

    same_date = [activity for activity in activities if activity.get('date') == payload['date']]
    if ticket:
        return next((activity for activity in same_date if existing_remote_id(activity) == ticket), None)

    state_key = identity_state_key(payload)
    stored_id = str(((identity_state or {}).get('activities') or {}).get(state_key) or '')
    if stored_id:
        stored_match = next(
            (activity for activity in same_date if str(activity.get('id')) == stored_id),
            None,
        )
        if stored_match:
            return stored_match
        append_warning(
            warnings,
            f'{sync_key}: local identity ledger points to missing MOCO activity {stored_id}; using migration fallbacks.',
        )

    # Migration path for entries created before sync keys stopped being exposed
    # as visible MOCO tags.
    legacy_matches = [
        activity for activity in same_date
        if sync_key
        and str(activity.get('tag') or '').upper() == sync_key
        and project_id(activity) == int(payload['project_id'])
    ]
    if len(legacy_matches) == 1:
        append_warning(warnings, f'{sync_key}: matched legacy visible sync-key tag; apply can migrate it.')
        return legacy_matches[0]

    # The sync key intentionally stays local. Match an untagged activity by its
    # user-facing description, which remains stable even when somebody moves it
    # to a more appropriate MOCO project or task after creation.
    description_matches = [
        activity for activity in same_date
        if (activity.get('description') or '') == payload['description']
    ]
    if len(description_matches) == 1:
        append_warning(warnings, f'{sync_key}: matched by description fallback; apply or --refresh-state will persist its MOCO ID locally.')
        return description_matches[0]
    if len(description_matches) > 1:
        exact_matches = [
            activity for activity in description_matches
            if project_id(activity) == int(payload['project_id'])
            and task_id(activity) == int(payload['task_id'])
        ]
        if len(exact_matches) == 1:
            append_warning(warnings, f'{sync_key}: matched duplicate description by project/task fallback; apply or --refresh-state will persist its MOCO ID locally.')
            return exact_matches[0]
    return None


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


def summarize_sync(results, desired, approved_seconds):
    desired_seconds = sum(item['seconds'] for item in desired)
    effective_seconds = sum(item['effective_seconds'] for item in results)
    projected_seconds = sum(item['projected_seconds'] for item in results)
    return {
        'activity_count': len(desired),
        'approved_seconds': approved_seconds,
        'desired_seconds': desired_seconds,
        'currently_stored_targeted_seconds': effective_seconds,
        'currently_stored_difference_seconds': effective_seconds - desired_seconds,
        'projected_targeted_seconds': projected_seconds,
        'projected_difference_seconds': projected_seconds - desired_seconds,
        'desired_billable_seconds': sum(item['seconds'] for item in desired if item['billable']),
        'desired_non_billable_seconds': sum(item['seconds'] for item in desired if not item['billable']),
        'actions': dict(sorted(Counter(item['action'] for item in results).items())),
    }


def synchronize(client, worklog, config, apply=False, update_existing=False, identity_state=None):
    default_date = worklog.get('date')
    if not default_date:
        raise ValueError('The worklog needs a top-level date.')
    identity_state = identity_state if identity_state is not None else {'version': 1, 'activities': {}}
    warnings = semantic_warnings(worklog, config)
    desired = [desired_payload(activity, default_date, config) for activity in worklog.get('activities') or []]
    if not desired:
        raise ValueError('The worklog contains no activities.')
    activity_keys = [item['_activity_key'] for item in desired]
    duplicates = sorted(key for key, count in Counter(activity_keys).items() if count > 1)
    if duplicates:
        raise ValueError(f"Duplicate activity identity in worklog: {', '.join(duplicates)}")
    approved_seconds = approved_total_seconds(worklog, desired, warnings)
    dates = sorted({item['date'] for item in desired})
    existing_by_date = {activity_date: client.activities(activity_date) for activity_date in dates}
    results = []

    for payload in desired:
        activity_key = payload['_activity_key']
        existing = find_existing(
            existing_by_date[payload['date']], payload, identity_state, warnings,
        )
        delta = differences(existing, payload) if existing else {}
        current_seconds = comparable(existing)['seconds'] if existing else 0
        if existing:
            remember_identity(identity_state, payload, existing)
        if existing and not delta:
            results.append({
                'action': 'unchanged',
                'id': existing.get('id'),
                'key': activity_key,
                'desired_seconds': payload['seconds'],
                'effective_seconds': current_seconds,
                'projected_seconds': current_seconds,
            })
            continue
        if existing and not update_existing:
            results.append({
                'action': 'preserved-existing',
                'id': existing.get('id'),
                'key': activity_key,
                'desired_seconds': payload['seconds'],
                'effective_seconds': current_seconds,
                'projected_seconds': current_seconds,
                'differences': delta,
            })
            continue
        if not apply:
            results.append({
                'action': 'would-update' if existing else 'would-create',
                'id': existing.get('id') if existing else None,
                'key': activity_key,
                'project': payload.get('_project_name'),
                'task': payload.get('_task_name'),
                'seconds': payload['seconds'],
                'desired_seconds': payload['seconds'],
                'effective_seconds': current_seconds,
                'projected_seconds': payload['seconds'],
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
        remember_identity(identity_state, payload, record)
        results.append({
            'action': action,
            'id': record.get('id'),
            'key': activity_key,
            'desired_seconds': payload['seconds'],
            'effective_seconds': payload['seconds'],
            'projected_seconds': payload['seconds'],
        })

    verification = {}
    if apply:
        for activity_date in dates:
            verification[activity_date] = client.activities(activity_date)
        for payload in desired:
            activity_key = payload['_activity_key']
            stored = find_existing(
                verification[payload['date']], payload, identity_state, warnings,
            )
            if not stored:
                raise RuntimeError(f"Verification failed: {activity_key} is missing after synchronization.")
            remember_identity(identity_state, payload, stored)
            delta = differences(stored, payload)
            matching_result = next(item for item in results if item['key'] == activity_key)
            if matching_result['action'] in ('created', 'updated', 'unchanged') and delta:
                raise RuntimeError(f"Verification failed for {activity_key}: {json.dumps(delta, ensure_ascii=False)}")

    return {
        'date': default_date,
        'apply': apply,
        'warnings': warnings,
        'summary': summarize_sync(results, desired, approved_seconds),
        'results': results,
    }


def main():
    parser = argparse.ArgumentParser(description='Idempotently synchronize an approved JSON worklog to MOCO.')
    parser.add_argument('worklog', help='Approved worklog JSON file, or - to read JSON from stdin')
    parser.add_argument('--config', help='Configuration file (default: config.json)')
    parser.add_argument('--state', help='Local non-Jira identity ledger (default: .moco-sync-state.json)')
    parser.add_argument(
        '--refresh-state',
        action='store_true',
        help='Persist read-only non-Jira identity matches without changing MOCO',
    )
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
    state_path = Path(args.state) if args.state else script_dir / '.moco-sync-state.json'
    identity_state = load_identity_state(state_path)
    api_key = resolve_api_key()
    base_url = ((config.get('moco') or {}).get('base_url') or 'https://3volutions.mocoapp.com')
    result = synchronize(
        MocoClient(base_url, api_key),
        worklog,
        config,
        args.apply,
        args.update_existing,
        identity_state,
    )
    if args.apply or args.refresh_state:
        save_identity_state(state_path, identity_state)
        result['identity_state_saved'] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
