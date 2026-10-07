import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import worklog


class ChatCollectionTests(unittest.TestCase):
    def test_t3_configuration_selects_t3_without_scanning_codex(self):
        with patch.dict(worklog.CONFIG, {'t3': {'sources': [{'name': 'one'}]}}, clear=True):
            self.assertEqual('t3', worklog.resolve_chat_source())
            with patch('t3_history.collect', return_value={'chats': [], 'sources': [{'status':'unavailable'}], 'warnings': ['unavailable']}) as t3, \
                 patch('worklog.analyze_codex_history') as codex:
                result = worklog.collect_chat_history('t3', datetime(2026,10,5,tzinfo=timezone.utc), timezone.utc, [])
            t3.assert_called_once()
            codex.assert_not_called()
            self.assertTrue(result['t3_history']['warnings'])

    def test_explicit_codex_investigation_never_reads_t3(self):
        with patch('t3_history.collect') as t3, \
             patch('worklog.analyze_codex_history', return_value=[]) as codex:
            worklog.collect_chat_history('codex', datetime(2026,10,5,tzinfo=timezone.utc), timezone.utc, [], Path('unused'))
        t3.assert_not_called()
        codex.assert_called_once()

    def test_legacy_configuration_and_explicit_overrides(self):
        with patch.dict(worklog.CONFIG, {}, clear=True):
            self.assertEqual('codex', worklog.resolve_chat_source())
            self.assertEqual('none', worklog.resolve_chat_source('none'))
        with patch.dict(worklog.CONFIG, {'chat_history': {'source': 't3'}}, clear=True):
            self.assertEqual('t3', worklog.resolve_chat_source())
            self.assertEqual('codex', worklog.resolve_chat_source('codex'))
            with self.assertRaises(ValueError):
                worklog.resolve_chat_source('both')

    def test_none_performs_no_chat_collection(self):
        with patch('t3_history.collect') as t3, patch('worklog.analyze_codex_history') as codex:
            result = worklog.collect_chat_history('none', datetime(2026,10,5,tzinfo=timezone.utc), timezone.utc, [])
        t3.assert_not_called()
        codex.assert_not_called()
        self.assertEqual('none', result['chat_source'])


if __name__ == '__main__':
    unittest.main()
