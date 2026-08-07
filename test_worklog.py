import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import worklog


def epoch(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute).timestamp()


def iso_timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


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

    def test_activitywatch_not_afk_rows_are_merged_before_summing(self):
        target_date = datetime(2026, 1, 15)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'activitywatch.db'
            connection = sqlite3.connect(database)
            connection.execute('CREATE TABLE buckets (id INTEGER, name TEXT, type TEXT)')
            connection.execute('CREATE TABLE events (bucketrow INTEGER, starttime INTEGER, endtime INTEGER, data TEXT)')
            connection.execute(
                'INSERT INTO buckets VALUES (?, ?, ?)',
                (1, 'aw-watcher-afk_andromeda', 'afkstatus'),
            )
            first = epoch(2026, 1, 15, 9)
            rows = [
                (1, int(first * 1e9), int((first + 3600) * 1e9), json.dumps({'status': 'not-afk'})),
                (1, int((first + 1800) * 1e9), int((first + 5400) * 1e9), json.dumps({'status': 'not-afk'})),
            ]
            connection.executemany('INSERT INTO events VALUES (?, ?, ?, ?)', rows)
            connection.commit()
            connection.close()

            result = worklog.analyze_day(database, target_date)

        self.assertEqual(7200, result['raw_total_active'])
        self.assertEqual(5400, result['total_active'])
        self.assertEqual(1, len(result['active_intervals']))

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
