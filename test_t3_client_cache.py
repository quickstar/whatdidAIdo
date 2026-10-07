import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import t3_client_cache
import t3_history


class T3ClientCacheTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
