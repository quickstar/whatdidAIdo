import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import worklog


def epoch(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute).timestamp()


def iso_timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


class FakeActivityWatchClient:
    def __init__(self, buckets, events, info=None):
        self.buckets = buckets
        self.events = events
        self.info = info or {
            'hostname': 'andromeda',
            'device_id': 'central-device',
            'version': 'v0.14.0b3 (rust)',
        }

    def get_info(self):
        return self.info

    def get_buckets(self):
        return self.buckets

    def get_events(self, bucket_id, start, end):
        return self.events.get(bucket_id, [])


def aw_event(timestamp, duration, data):
    return {
        'timestamp': timestamp.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'duration': duration,
        'data': data,
    }


class CodexHistoryTests(unittest.TestCase):
    def setUp(self):
        self.original_config = worklog.CONFIG
        worklog.CONFIG = {
            'ticket_prefixes': {'ITEM': 'Feature', 'ROMSD': 'Bug'},
            'projects': {'rooms': '3V-ROOMS'},
        }

    def tearDown(self):
        worklog.CONFIG = self.original_config

    def create_state_database(self, codex_home):
        connection = sqlite3.connect(codex_home / 'state_5.sqlite')
        connection.execute('''
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                rollout_path TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                name TEXT,
                first_user_message TEXT,
                git_branch TEXT,
                git_origin_url TEXT,
                thread_source TEXT,
                agent_path TEXT,
                source TEXT
            )
        ''')
        return connection

    def write_rollout(self, path, entries):
        path.write_text(
            '\n'.join(json.dumps(entry) for entry in entries) + '\n{"partial"',
            encoding='utf-8',
        )

    def test_root_tasks_are_loaded_with_afk_overlap_and_subagents_excluded(self):
        target_date = datetime(2026, 1, 15)
        task_start = epoch(2026, 1, 15, 9)
        task_end = epoch(2026, 1, 15, 9, 30)

        with tempfile.TemporaryDirectory() as directory:
            codex_home = Path(directory)
            root_rollout = codex_home / 'root.jsonl'
            subagent_rollout = codex_home / 'subagent.jsonl'
            entries = [
                {
                    'timestamp': iso_timestamp(task_start),
                    'type': 'event_msg',
                    'payload': {
                        'type': 'task_started',
                        'turn_id': 'turn-1',
                        'started_at': task_start,
                    },
                },
                {
                    'timestamp': iso_timestamp(task_start + 5),
                    'type': 'event_msg',
                    'payload': {
                        'type': 'user_message',
                        'message': 'Implement ITEM-123 cleanly',
                    },
                },
                {
                    'timestamp': iso_timestamp(task_end),
                    'type': 'event_msg',
                    'payload': {
                        'type': 'task_complete',
                        'turn_id': 'turn-1',
                        'started_at': task_start,
                        'completed_at': task_end,
                        'last_agent_message': 'ITEM-123 is complete. Compared with ROMSD-999.',
                    },
                },
            ]
            self.write_rollout(root_rollout, entries)
            self.write_rollout(subagent_rollout, entries)

            connection = self.create_state_database(codex_home)
            rows = [
                (
                    'root', str(root_rollout), epoch(2025, 12, 1, 9), task_end,
                    r'\\?\D:\git\rooms', 'Implement ITEM-123', None,
                    'Implement ITEM-123', 'feature/ITEM-123', None, 'user', None, 'vscode',
                ),
                (
                    'subagent', str(subagent_rollout), task_start, task_end,
                    r'\\?\D:\git\rooms', 'Implement ITEM-123', None,
                    'Implement ITEM-123', 'feature/ITEM-123', None, 'subagent',
                    '/root/reviewer', '{"subagent": {}}',
                ),
                (
                    'old', str(root_rollout), epoch(2025, 12, 1, 9),
                    epoch(2026, 1, 14, 17), r'\\?\D:\git\rooms', 'Old task', None,
                    'Old task', None, None, 'user', None, 'vscode',
                ),
            ]
            connection.executemany(
                'INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                rows,
            )
            connection.commit()
            connection.close()

            active_intervals = [(datetime(2026, 1, 15, 9, 10), 600)]
            tasks = worklog.analyze_codex_history(
                codex_home,
                target_date,
                active_intervals,
            )

        self.assertEqual(1, len(tasks))
        task = tasks[0]
        self.assertEqual('root', task['thread_id'])
        self.assertEqual('3V-ROOMS', task['workspace'])
        self.assertEqual(['ITEM-123'], task['tickets'])
        self.assertEqual('completed', task['status'])
        self.assertEqual(1800, task['span_seconds'])
        self.assertEqual(600, task['active_seconds'])
        self.assertIn('ROMSD-999', task['outcome'])

    def test_ai_output_labels_codex_time_as_overlapping_context(self):
        task = {
            'start': datetime(2026, 1, 15, 9),
            'end': datetime(2026, 1, 15, 9, 30),
            'workspace': '3V-ROOMS',
            'tickets': ['ITEM-123'],
            'branch': 'feature/ITEM-123',
            'status': 'completed',
            'span_seconds': 1800,
            'active_seconds': 600,
            'title': 'Implement ITEM-123',
            'outcome': 'Implementation verified.',
        }

        output = io.StringIO()
        with redirect_stdout(output):
            worklog.print_codex_tasks_ai([task])

        rendered = output.getvalue()
        self.assertIn('semantic context; spans and overlaps may overlap', rendered)
        self.assertIn('30m span / 10m not-AFK overlap', rendered)
        self.assertIn('branch feature/ITEM-123', rendered)

    def test_activitywatch_sources_are_discovered_deduplicated_and_unioned(self):
        target_date = datetime(2026, 1, 15)
        zurich = worklog.resolve_timezone('Europe/Zurich')
        local_start = datetime(2026, 1, 15, 9, tzinfo=zurich)
        remote_start = datetime(2026, 1, 15, 9, 30, tzinfo=zurich)
        buckets = [
            {'id': 'aw-watcher-afk_andromeda', 'type': 'afkstatus', 'hostname': 'andromeda'},
            {'id': 'aw-watcher-window_andromeda', 'type': 'currentwindow', 'hostname': 'andromeda'},
            {
                'id': 'aw-watcher-afk_Gamer-synced-from-Gamer',
                'type': 'afkstatus',
                'hostname': 'Gamer',
                'data': {'$aw.sync.origin': 'Gamer'},
            },
            {
                'id': 'aw-watcher-afk_Gamer-copy-synced-from-Gamer',
                'type': 'afkstatus',
                'hostname': 'Gamer',
                'data': {'$aw.sync.origin': 'Gamer'},
            },
            {'id': 'aw-watcher-window_Gamer-synced-from-Gamer', 'type': 'currentwindow', 'hostname': 'Gamer'},
            {'id': 'aw-watcher-web-edge_Gamer-synced-from-Gamer', 'type': 'web.tab.current', 'hostname': 'Gamer'},
            {'id': 'aw-watcher-datagrip_Gamer-synced-from-Gamer', 'type': 'app.editor.activity', 'hostname': 'Gamer'},
        ]
        remote_afk = aw_event(remote_start, 3600, {'status': 'not-afk'})
        events = {
            'aw-watcher-afk_andromeda': [aw_event(local_start, 3600, {'status': 'not-afk'})],
            'aw-watcher-window_andromeda': [aw_event(local_start, 1800, {'app': 'Code.exe', 'title': 'ITEM-123'})],
            'aw-watcher-afk_Gamer-synced-from-Gamer': [remote_afk],
            'aw-watcher-afk_Gamer-copy-synced-from-Gamer': [remote_afk],
            'aw-watcher-window_Gamer-synced-from-Gamer': [
                aw_event(remote_start, 1800, {'app': 'rider64.exe', 'title': 'ROMSD-456'})
            ],
            'aw-watcher-web-edge_Gamer-synced-from-Gamer': [
                aw_event(remote_start, 300, {'url': 'https://example.test/browse/ROMSD-456', 'title': 'ROMSD-456'})
            ],
            'aw-watcher-datagrip_Gamer-synced-from-Gamer': [
                aw_event(remote_start, 120, {'file': 'query.sql'})
            ],
        }

        result = worklog.analyze_day(FakeActivityWatchClient(buckets, events), target_date, zurich)

        self.assertEqual(['Gamer', 'andromeda'], result['activitywatch_sources'])
        self.assertEqual(7200, result['raw_total_active'])
        self.assertEqual(5400, result['total_active'])
        self.assertEqual(1800, result['activitywatch_cross_source_overlap'])
        self.assertEqual(1, result['activitywatch_duplicate_events'])
        self.assertEqual(1, len(result['active_intervals']))
        self.assertEqual(1800, result['app_time']['rider64.exe'])
        self.assertEqual(300, result['jira_tickets']['ROMSD-456'])
        self.assertEqual(120, result['file_time']['query.sql'])
        self.assertEqual(1800, result['activitywatch_source_app_time']['Gamer']['rider64.exe'])
        self.assertEqual(2100, result['activitywatch_source_tickets']['Gamer']['ROMSD-456'])
        self.assertEqual(300, result['activitywatch_source_domain_time']['Gamer']['example.test'])
        self.assertEqual(120, result['activitywatch_source_file_time']['Gamer']['query.sql'])
        self.assertEqual(['Gamer'], worklog.activitywatch_ticket_sources(result, 'ROMSD-456'))

        output = io.StringIO()
        with redirect_stdout(output):
            worklog.print_ai_summary_v2(result, target_date)
        rendered = output.getvalue()
        self.assertIn('| Category | Client/Ticket | Source | Description | Time |', rendered)
        self.assertIn(
            '| Bug Fix | [ROMSD-456](https://3volutions.atlassian.net/browse/ROMSD-456) | Gamer |',
            rendered,
        )

    def test_activitywatch_source_aliases_normalize_before_deduplication(self):
        worklog.CONFIG['activitywatch'] = {
            'source_aliases': {
                'mac': 'macbook-pro',
                'Macbook': 'macbook-pro',
                'device-guid': 'macbook-pro',
            },
            'expected_sources': ['macbook-pro', 'missing-laptop'],
        }
        zurich = worklog.resolve_timezone('Europe/Zurich')
        start = datetime(2026, 1, 15, 9, tzinfo=zurich)
        buckets = [
            {
                'id': 'aw-watcher-afk_Mac-synced-from-Mac',
                'type': 'afkstatus',
                'hostname': 'Mac',
                'data': {'$aw.sync.origin': 'Mac'},
            },
            {
                'id': 'aw-watcher-window_Mac-synced-from-Mac',
                'type': 'currentwindow',
                'hostname': 'Mac',
            },
            {
                'id': 'aw-watcher-web_Macbook-synced-from-Macbook',
                'type': 'web.tab.current',
                'hostname': 'Macbook',
            },
            {
                'id': 'aw-watcher-web-synced-from-device-guid',
                'type': 'web.tab.current',
                'hostname': 'device-guid',
            },
        ]
        duplicate_web_event = aw_event(
            start, 60, {'url': 'https://example.test/', 'title': 'Example'}
        )
        events = {
            buckets[0]['id']: [aw_event(start, 600, {'status': 'not-afk'})],
            buckets[1]['id']: [aw_event(start, 600, {'app': 'Code.exe', 'title': 'Code'})],
            buckets[2]['id']: [duplicate_web_event],
            buckets[3]['id']: [duplicate_web_event],
        }
        client = FakeActivityWatchClient(buckets, events)

        result = worklog.analyze_day(client, datetime(2026, 1, 15), zurich)
        health = worklog.activitywatch_health(client)

        self.assertEqual(['macbook-pro'], result['activitywatch_sources'])
        self.assertEqual(
            {'Mac': 'macbook-pro', 'Macbook': 'macbook-pro', 'device-guid': 'macbook-pro'},
            result['activitywatch_source_aliases'],
        )
        self.assertEqual({'afk': 1, 'web': 2, 'window': 1}, result['activitywatch_bucket_counts']['macbook-pro'])
        self.assertEqual(1, result['activitywatch_duplicate_events'])
        self.assertFalse(any(
            'missing expected desktop bucket types' in warning
            for warning in result['activitywatch_warnings']
        ))
        self.assertIn(
            'Expected ActivityWatch sources are absent: missing-laptop',
            result['activitywatch_warnings'],
        )
        self.assertEqual(['macbook-pro'], list(health['sources']))
        self.assertEqual(result['activitywatch_source_aliases'], health['source_aliases'])
        self.assertIn(
            'Expected ActivityWatch sources are absent: missing-laptop',
            health['coverage_warnings'],
        )

    def test_activitywatch_source_alias_cycles_are_rejected(self):
        worklog.CONFIG['activitywatch'] = {
            'source_aliases': {'Mac': 'Macbook', 'Macbook': 'Mac'}
        }
        with self.assertRaisesRegex(worklog.ActivityWatchAPIError, 'Cyclic'):
            worklog.get_activitywatch_source_aliases()

    def test_activitywatch_expected_sources_must_be_a_string_list(self):
        worklog.CONFIG['activitywatch'] = {'expected_sources': 'andromeda'}
        with self.assertRaisesRegex(worklog.ActivityWatchAPIError, 'expected_sources'):
            worklog.get_activitywatch_expected_sources()

    def test_activitywatch_events_are_clipped_to_local_day_and_dst_bounds_are_correct(self):
        zurich = worklog.resolve_timezone('Europe/Zurich')
        target_date = datetime(2026, 3, 29)
        day_start, day_end = worklog.day_datetime_bounds(target_date, zurich)
        self.assertEqual(23 * 3600, day_end.timestamp() - day_start.timestamp())
        autumn_start, autumn_end = worklog.day_datetime_bounds(datetime(2026, 10, 25), zurich)
        self.assertEqual(25 * 3600, autumn_end.timestamp() - autumn_start.timestamp())

        bucket = {'id': 'aw-watcher-afk_andromeda', 'type': 'afkstatus', 'hostname': 'andromeda'}
        event = aw_event(day_start - timedelta(minutes=10), 20 * 60, {'status': 'not-afk'})
        result = worklog.analyze_day(
            FakeActivityWatchClient([bucket], {bucket['id']: [event]}),
            target_date,
            zurich,
        )
        self.assertEqual(600, result['total_active'])

    def test_rest_adapter_uses_bounded_read_only_event_request(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'[]'

        client = worklog.ActivityWatchRESTClient('127.0.0.1', 5600)
        start = datetime(2026, 1, 15, tzinfo=timezone.utc)
        end = datetime(2026, 1, 16, tzinfo=timezone.utc)
        with patch('worklog.urlopen', return_value=Response()) as mocked:
            self.assertEqual([], client.get_events('bucket/id', start, end))

        request = mocked.call_args.args[0]
        self.assertEqual('GET', request.method)
        self.assertIn('bucket%2Fid/events', request.full_url)
        self.assertIn('limit=-1', request.full_url)

    def test_malformed_events_and_missing_desktop_buckets_are_warnings(self):
        zurich = worklog.resolve_timezone('Europe/Zurich')
        bucket = {'id': 'aw-watcher-web_Gamer-synced-from-Gamer', 'type': 'web.tab.current', 'hostname': 'Gamer'}
        result = worklog.analyze_day(
            FakeActivityWatchClient([bucket], {bucket['id']: [{'duration': 'invalid'}]}),
            datetime(2026, 1, 15),
            zurich,
        )
        self.assertEqual(1, result['activitywatch_malformed_events'])
        self.assertTrue(any('missing expected desktop bucket types' in warning for warning in result['activitywatch_warnings']))

    def test_live_zero_duration_heartbeats_are_not_malformed(self):
        zurich = worklog.resolve_timezone('Europe/Zurich')
        bucket = {'id': 'aw-watcher-afk_andromeda', 'type': 'afkstatus', 'hostname': 'andromeda'}
        event = {
            'timestamp': '2026-01-15T12:00:00Z',
            'duration': 0.0,
            'data': {'status': 'not-afk'},
        }
        result = worklog.analyze_day(
            FakeActivityWatchClient([bucket], {bucket['id']: [event]}),
            datetime(2026, 1, 15),
            zurich,
        )
        self.assertEqual(0, result['activitywatch_malformed_events'])
        self.assertEqual(0, result['total_active'])

    def test_rest_adapter_reports_unavailable_server(self):
        client = worklog.ActivityWatchRESTClient('127.0.0.1', 5600)
        with patch('worklog.urlopen', side_effect=URLError('offline')):
            with self.assertRaisesRegex(worklog.ActivityWatchAPIError, 'start aw-server-rust'):
                client.get_info()

    def test_evidence_union_adds_only_concrete_completed_codex_tasks(self):
        active_start = datetime(2026, 1, 15, 9)
        first = active_start.timestamp()
        results = {
            'active_intervals': [(active_start, 3600)],
            'codex_tasks': [
                {
                    'thread_id': 'qualified',
                    'status': 'completed',
                    'tickets': ['ITEM-123'],
                    'branch': 'feature/ITEM-123',
                    'cwd': r'D:\git\rooms',
                    'outcome': 'Implemented and verified the fix.',
                    'spans': [(first + 1800, first + 7200)],
                },
                {
                    'thread_id': 'background-wait',
                    'status': 'active',
                    'tickets': ['ITEM-999'],
                    'branch': 'feature/ITEM-999',
                    'cwd': r'D:\git\rooms',
                    'outcome': '',
                    'spans': [(first + 7200, first + 10800)],
                },
            ],
        }

        evidence = worklog.calculate_evidence_union(results)

        self.assertEqual(7200, evidence['evidence_union_seconds'])
        self.assertEqual(['qualified'], evidence['evidence_union_codex_task_ids'])


if __name__ == '__main__':
    unittest.main()
