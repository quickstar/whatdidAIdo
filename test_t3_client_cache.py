import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import t3_client_cache
import t3_history


class T3ClientCacheTests(unittest.TestCase):
    def test_latest_raw_records_precede_blob_decoding_and_keep_tombstones(self):
        # Physical iteration order need not match sequence order. An obsolete
        # body can refer to a blob Chromium has already deleted.
        old_body = SimpleNamespace(user_key=b'body', seq=1, value='missing-blob')
        new_body = SimpleNamespace(user_key=b'body', seq=4, value='current-blob')
        old_ref = SimpleNamespace(user_key=b'blob-ref', seq=2, value='old-path')
        new_ref = SimpleNamespace(user_key=b'blob-ref', seq=5, value='new-path')
        old_deleted = SimpleNamespace(user_key=b'gone', seq=3, value='obsolete')
        tombstone = SimpleNamespace(user_key=b'gone', seq=6, value=None)
        selected = t3_client_cache.latest_raw_records(
            [new_body, old_body, new_ref, old_ref, tombstone, old_deleted])
        self.assertEqual({b'body': new_body, b'blob-ref': new_ref, b'gone': tombstone},
                         {r.user_key: r for r in selected})

    def record(self, key, sequence, value, live=True):
        return SimpleNamespace(key=SimpleNamespace(value=key), ldb_seq_no=sequence,
                               value=value, is_live=live)

    def test_latest_deletion_wins_over_older_live_data(self):
        rows = [self.record('gone',1,'{}'), self.record('gone',3,None,False),
                self.record('gone',2,'{}'), self.record('kept',2,'{"title":"new"}'),
                self.record('kept',1,'{"title":"old"}')]
        self.assertEqual({'kept':{'title':'new'}}, t3_client_cache.latest_documents(rows))

    def test_v8_one_byte_strings_preserve_umlauts(self):
        self.assertEqual({'a':{'title':'Änderung'}}, t3_client_cache.latest_documents([
            self.record('a',1,'{"title":"Änderung"}'.encode('latin-1'))]))

    def test_authoritative_database_environment_skips_cached_json_decoding(self):
        records = [self.record('local:thread',1,'not-json-never-decoded'),
                   self.record('remote:thread',2,'{"title":"Remote"}')]
        self.assertEqual({'remote:thread':{'title':'Remote'}},
                         t3_client_cache.latest_documents(records, {'local'}))

    def test_remote_cached_body_is_included_but_missing_body_is_flagged(self):
        shell = {'updatedAt':'2026-10-07T00:00:00Z', 'projects':[{'id':'p','title':'rooms','workspaceRoot':'/work/rooms'}],
                 'threads':[{'id':i,'createdAt':'2026-10-04T00:00:00Z','updatedAt':'2026-10-05T12:00:00Z'} for i in ('t','missing')]}
        thread = {'id':'t','title':'Remote review','projectId':'p','branch':'develop',
                  'session':{'providerName':'claudeAgent'}, 'messages':[
                      {'id':'m','turnId':'turn','role':'user','text':'Review this',
                       'createdAt':'2026-10-05T08:00:00Z','updatedAt':'2026-10-05T08:00:00Z'}]}
        cache = {'shell':{'env':{'snapshot':shell}},
                 'thread':{'env:t':{'environmentId':'env','snapshot':{'thread':thread,'page':{'hasMore':True}}}}}
        with patch('t3_client_cache.read', return_value=cache):
            result = t3_history.collect([{'name':'orion','environment_id':'env'}],
                datetime(2026,10,5,tzinfo=timezone.utc), datetime(2026,10,6,tzinfo=timezone.utc),
                {'indexeddb_directory':'unused'})
        self.assertEqual(['orion'],result['chats'][0]['sources'])
        self.assertEqual('unknown',result['chats'][0]['client_device'])
        self.assertEqual('cached_partial',result['sources'][0]['status'])
        self.assertEqual(['missing'],result['sources'][0]['possibly_relevant_uncached_threads'])
        self.assertEqual(['t'],result['sources'][0]['truncated_thread_ids'])
        self.assertTrue(result['warnings'])

    def test_v2_projection_messages_pagination_and_archived_shell(self):
        stamp = '2026-10-19T08:00:00Z'
        thread = {'id': 't', 'title': 'Integration', 'projectId': 'p',
                  'createdAt': stamp, 'updatedAt': stamp,
                  'lineage': {'relationshipToParent': None}}
        message = {'id': 'm', 'runId': 'r', 'role': 'user', 'text': 'Review',
                   'createdBy': 'user', 'createdAt': stamp, 'updatedAt': stamp}
        cache = {
            'shell': {'env': {'snapshot': {
                'projects': [{'id': 'p', 'title': 'equipt'}],
                'threads': [thread],
                'archivedThreads': [dict(thread, id='archived')]}}},
            'thread': {'env:t': {'environmentId': 'env', 'snapshot': {
                'projection': {'thread': thread, 'messages': [message],
                               'providerThreads': [{'driver': 'codex'}],
                               'updatedAt': stamp},
                'hasMoreHistory': True}}}}
        latest, rows, coverage = t3_client_cache.rows_for_source(
            cache, 'env', datetime(2026,10,19,tzinfo=timezone.utc),
            datetime(2026,10,20,tzinfo=timezone.utc))
        self.assertEqual('Review', rows[0]['text'])
        self.assertEqual('user', rows[0]['created_by'])
        self.assertEqual('codex', rows[0]['provider_name'])
        self.assertEqual(['t'], coverage['truncated_thread_ids'])
        self.assertEqual(['archived'], coverage['possibly_relevant_uncached_threads'])
        self.assertEqual(1, coverage['archived_threads'])
        self.assertIsNone(coverage['shell_updated_at'])
        self.assertIsNotNone(latest)


if __name__ == '__main__':
    unittest.main()
