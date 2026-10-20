import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from urllib.error import HTTPError
import t3_live


class LiveTests(unittest.TestCase):
    def test_active_and_archived_threads_are_deduplicated_and_day_filtered(self):
        stamp = '2026-10-19T10:00:00Z'
        thread = dict(id='a', createdAt=stamp, updatedAt=stamp)
        old = dict(id='old', createdAt='2026-10-01T00:00:00Z',
                   updatedAt='2026-10-02T00:00:00Z')
        client = Mock()
        client.shell.return_value = dict(projects=[], threads=[thread, old])
        client.archived.return_value = dict(projects=[], threads=[thread, dict(thread, id='b')])
        client.thread.side_effect = lambda key: {'projection': {
            'thread': dict(thread, id=key), 'messages': []}}
        cache, coverage = t3_live.collect(
            client, 'env', datetime(2026,10,19,tzinfo=timezone.utc),
            datetime(2026,10,20,tzinfo=timezone.utc))
        self.assertEqual(2, client.thread.call_count)
        self.assertEqual({'env:a', 'env:b'}, set(cache['thread']))
        self.assertTrue(coverage['complete'])
        self.assertEqual(2, coverage['requested_threads'])

    def test_archive_or_thread_failure_is_explicit_and_keeps_good_evidence(self):
        stamp = '2026-10-19T10:00:00Z'
        client = Mock()
        client.shell.return_value = dict(projects=[], threads=[
            dict(id=k, createdAt=stamp, updatedAt=stamp) for k in ['a','b']])
        client.archived.side_effect = RuntimeError('unavailable')
        client.thread.side_effect = [{'projection': {'thread': {'id':'a'}, 'messages':[]}},
                                     RuntimeError('unavailable')]
        cache, coverage = t3_live.collect(
            client, 'env', datetime(2026,10,19,tzinfo=timezone.utc),
            datetime(2026,10,20,tzinfo=timezone.utc))
        self.assertFalse(coverage['complete'])
        self.assertFalse(coverage['archived_verified'])
        self.assertEqual(['b'], coverage['failed_thread_ids'])
        self.assertEqual(['env:a'], list(cache['thread']))

    def test_credentials_cannot_be_sent_to_a_different_host(self):
        catalog = {'profiles': [dict(environmentId='env', connectionId='id',
            _tag='BearerConnectionProfile', httpBaseUrl='https://other.invalid')],
            'credentials': [dict(connectionId='id', credential={
                '_tag':'BearerConnectionCredential','token':'DO-NOT-PRINT'})]}
        with self.assertRaisesRegex(RuntimeError, 'matching saved'):
            t3_live.Client(catalog, 'env', 'expected.invalid')

    def test_http_errors_do_not_expose_authentication_headers(self):
        client = object.__new__(t3_live.Client)
        client.base_url = 'https://expected.invalid'
        client._token = 'DO-NOT-PRINT'
        client.timeout = 1
        client.opener = Mock()
        client.opener.open.side_effect = HTTPError(
            client.base_url, 401, 'DO-NOT-PRINT', {}, None)
        with self.assertRaisesRegex(RuntimeError, r'HTTP 401') as caught:
            client.shell()
        self.assertNotIn('DO-NOT-PRINT', str(caught.exception))
        request = client.opener.open.call_args.args[0]
        self.assertEqual('GET', request.method)

    def test_http_redirects_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'redirect refused'):
            t3_live.NoRedirect().redirect_request(
                None, None, 302, 'Found', {}, 'https://other.invalid')

    def test_archived_rpc_disables_redirects_and_closes_connection(self):
        client = object.__new__(t3_live.Client)
        client.base_url = 'https://expected.invalid'
        client._token = 'DO-NOT-PRINT'
        client.timeout = 1
        socket = Mock()
        socket.recv.return_value = (
            '{"_tag":"Exit","requestId":"0","exit":'
            '{"_tag":"Success","value":{"threads":[]}}}')
        module = Mock()
        module.create_connection.return_value = socket
        with patch.dict('sys.modules', websocket=module):
            self.assertEqual({'threads': []}, client.archived())
        self.assertEqual(0, module.create_connection.call_args.kwargs['redirect_limit'])
        socket.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
