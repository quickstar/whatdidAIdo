import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import t3_history


class T3HistoryTests(unittest.TestCase):
    def database(self, root):
        path = Path(root) / 'state.sqlite'
        with closing(sqlite3.connect(path)) as c:
            c.executescript('''
                CREATE TABLE projection_projects(project_id TEXT, title TEXT, workspace_root TEXT);
                CREATE TABLE projection_threads(thread_id TEXT, project_id TEXT, title TEXT, branch TEXT);
                CREATE TABLE projection_thread_messages(message_id TEXT, thread_id TEXT, turn_id TEXT,
                    role TEXT, text TEXT, created_at TEXT, updated_at TEXT);
                CREATE TABLE projection_thread_sessions(thread_id TEXT, provider_name TEXT);
                INSERT INTO projection_projects VALUES('p','rooms','/work/rooms');
                INSERT INTO projection_threads VALUES('t','p','Fix Series Reattachment Data Loss','ITEM-2994');
                INSERT INTO projection_thread_sessions VALUES('t','claudeAgent');
                INSERT INTO projection_thread_messages VALUES('m1','t','turn','user','Fix ITEM-2994',
                    '2026-10-05T08:00:00Z','2026-10-05T08:00:00Z');
                INSERT INTO projection_thread_messages VALUES('m2','t','turn','assistant','Tests passed',
                    '2026-10-05T20:00:00Z','2026-10-05T20:00:00Z');
            ''')
        return path

    def collect(self, sources):
        return t3_history.collect(sources, datetime(2026, 10, 5, tzinfo=timezone.utc),
                                  datetime(2026, 10, 6, tzinfo=timezone.utc))

    def test_claude_chat_is_included_without_turn_runtime_becoming_work_time(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.database(root)
            result = self.collect([{'name': 'andromeda', 'database': str(path)}])
            chat = result['chats'][0]
            self.assertEqual('Fix Series Reattachment Data Loss', chat['title'])
            self.assertEqual(['claudeAgent'], chat['providers'])
            self.assertEqual(['ITEM-2994'], chat['tickets'])
            self.assertEqual(1, chat['user_message_count'])
            self.assertNotIn('work_seconds', chat)
            self.assertNotIn('spans', chat)
            self.assertEqual('unknown', chat['client_device'])

    def test_same_chat_exported_from_two_servers_is_not_counted_twice(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.database(root)
            result = self.collect([{'name': 'one', 'database': str(path)},
                                   {'name': 'migrated', 'database': str(path)}])
            self.assertEqual(1, len(result['chats']))
            self.assertEqual(['migrated', 'one'], result['chats'][0]['sources'])
            self.assertEqual(1, result['chats'][0]['user_message_count'])
            self.assertEqual(2, result['duplicate_messages_removed'])

    def test_two_aliases_of_same_database_are_read_only_once(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.database(root)
            with patch('t3_history._read', wraps=t3_history._read) as read:
                result = self.collect([{'name':'one','database':str(path)},
                                       {'name':'alias','database':str(path)}])
            read.assert_called_once()
            self.assertEqual(1, result['collection_stats']['database_reads'])

    def test_unavailable_expected_server_is_a_warning_not_zero_work(self):
        result = self.collect([{'name': 'orion', 'endpoint': 'https://orion.example'}])
        self.assertEqual('unavailable', result['sources'][0]['status'])
        self.assertTrue(result['warnings'])

    def test_midnight_and_updated_old_messages_are_handled(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.database(root)
            with closing(sqlite3.connect(path)) as c:
                c.execute("INSERT INTO projection_thread_messages VALUES(?,?,?,?,?,?,?)",
                          ('previous','t','previous','assistant','Still running',
                           '2026-10-04T21:00:00Z','2026-10-05T02:00:00Z'))
                c.execute("INSERT INTO projection_thread_messages VALUES(?,?,?,?,?,?,?)",
                          ('next','t','next','user','Tomorrow',
                           '2026-10-06T00:00:00Z','2026-10-06T00:00:00Z'))
                c.commit()
            result = self.collect([{'name': 'one', 'database': str(path)}])
            chat = result['chats'][0]
            self.assertEqual(3, chat['message_count'])
            self.assertEqual(1, chat['user_message_count'])
            self.assertTrue(any(m['continued_from_previous_day'] for m in chat['messages']))

    def test_database_is_read_only_and_missing_file_is_not_created(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'missing.sqlite'
            self.assertTrue(self.collect([{'name':'one','database':str(path)}])['warnings'])
            self.assertFalse(path.exists())

    def test_day_boundary_uses_timezone_not_timestamp_string_order(self):
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as root:
            path = self.database(root)
            with closing(sqlite3.connect(path)) as c:
                c.execute("INSERT INTO projection_thread_messages VALUES(?,?,?,?,?,?,?)",
                          ('local-midnight','t','turn','user','At local midnight',
                           '2026-10-04T22:00:00Z','2026-10-04T22:00:00Z'))
                c.commit()
            local = timezone(timedelta(hours=2))
            result = t3_history.collect([{'name':'one','database':str(path)}],
                         datetime(2026,10,5,tzinfo=local), datetime(2026,10,6,tzinfo=local))
            self.assertEqual(2, result['chats'][0]['user_message_count'])

    def test_broken_source_does_not_suppress_readable_sources(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.database(root)
            result = self.collect([{'name':'broken','database':str(Path(root)/'absent')},
                                   {'name':'one','database':str(path)}])
            self.assertEqual(1, len(result['chats']))
            self.assertEqual('readable', result['sources'][1]['status'])
            self.assertTrue(result['warnings'])

    def test_post_day_revision_is_explicitly_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.database(root)
            with closing(sqlite3.connect(path)) as c:
                c.execute("UPDATE projection_thread_messages SET updated_at='2026-10-07T01:00:00Z' WHERE message_id='m2'")
                c.commit()
            result = self.collect([{'name':'one','database':str(path)}])
            self.assertTrue(result['chats'][0]['messages'][1]['updated_after_requested_day'])
            self.assertTrue(result['warnings'])


if __name__ == '__main__':
    unittest.main()
