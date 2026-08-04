import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import worklog_db


def epoch(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute).timestamp()


def iso_timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')


class CodexHistoryTests(unittest.TestCase):
    def setUp(self):
        self.original_config = worklog_db.CONFIG
        worklog_db.CONFIG = {
            'ticket_prefixes': {'ITEM': 'Feature', 'ROMSD': 'Bug'},
            'projects': {'rooms': '3V-ROOMS'},
        }

    def tearDown(self):
        worklog_db.CONFIG = self.original_config

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
            tasks = worklog_db.analyze_codex_history(
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
            worklog_db.print_codex_tasks_ai([task])

        rendered = output.getvalue()
        self.assertIn('semantic context; spans and overlaps may overlap', rendered)
        self.assertIn('30m span / 10m not-AFK overlap', rendered)
        self.assertIn('branch feature/ITEM-123', rendered)


if __name__ == '__main__':
    unittest.main()
