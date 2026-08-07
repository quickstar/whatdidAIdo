import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import github_audit


class GitHubAuditUtilityTests(unittest.TestCase):
    def test_zurich_day_bounds_handle_normal_and_dst_transition_days(self):
        summer_start, summer_end = github_audit.local_day_utc_bounds(date(2026, 7, 28))
        self.assertEqual('2026-07-27T22:00:00+00:00', summer_start.isoformat())
        self.assertEqual(timedelta(hours=24), summer_end - summer_start)

        spring_start, spring_end = github_audit.local_day_utc_bounds(date(2026, 3, 29))
        self.assertEqual('2026-03-28T23:00:00+00:00', spring_start.isoformat())
        self.assertEqual(timedelta(hours=23), spring_end - spring_start)

        autumn_start, autumn_end = github_audit.local_day_utc_bounds(date(2026, 10, 25))
        self.assertEqual('2026-10-24T22:00:00+00:00', autumn_start.isoformat())
        self.assertEqual(timedelta(hours=25), autumn_end - autumn_start)

    def test_paginated_gh_json_documents_are_decoded_and_flattened(self):
        raw = json.dumps({'items': [{'sha': 'one'}]}) + '\n' + json.dumps({'items': [{'sha': 'two'}]})
        values = github_audit.decode_json_stream(raw)
        self.assertEqual(['one', 'two'], [item['sha'] for item in github_audit.flatten_pages(values, 'items')])

    def test_repository_names_are_extracted_from_api_and_remote_urls(self):
        self.assertEqual(
            '3volutionsAG/rooms',
            github_audit.repo_from_api_url('https://api.github.com/repos/3volutionsAG/rooms'),
        )
        self.assertEqual(
            '3volutionsAG/rooms',
            github_audit.repo_from_remote('git@github.com:3volutionsAG/rooms.git'),
        )

    def test_commit_evidence_is_deduplicated_by_repository_and_sha(self):
        audit = github_audit.GitHubAudit(date(2026, 7, 28), {})
        audit.add_commit('org/repo', {'sha': 'abc'}, 'search')
        audit.add_commit('org/repo', {'sha': 'abc'}, 'push')
        result = audit.result()
        self.assertEqual(1, len(result['commits']))
        self.assertEqual(['search', 'push'], result['commits'][0]['evidence'])

    def test_pr_discovery_filters_to_local_day_and_uses_committer_date_for_commits(self):
        audit = github_audit.GitHubAudit(date(2026, 7, 28), {}, login='quickstar')

        def fake_gh(path, method='GET', fields=None, paginate=False):
            if path == '/search/issues':
                return [{
                    'items': [
                        {
                            'repository_url': 'https://api.github.com/repos/org/repo',
                            'number': 1,
                            'title': 'Inside',
                            'updated_at': '2026-07-28T10:00:00Z',
                        },
                        {
                            'repository_url': 'https://api.github.com/repos/org/other',
                            'number': 2,
                            'title': 'Outside',
                            'updated_at': '2026-07-29T10:00:00Z',
                        },
                    ]
                }]
            if path == 'repos/org/repo/pulls/1':
                return {'title': 'Inside', 'state': 'closed', 'merged_at': '2026-07-28T12:00:00Z'}
            if path in {
                'repos/org/repo/issues/1/comments?per_page=100',
                'repos/org/repo/pulls/1/reviews?per_page=100',
                'repos/org/repo/pulls/1/comments?per_page=100',
            }:
                return [[]]
            if path == 'repos/org/repo/pulls/1/commits?per_page=100':
                return [[
                    {
                        'sha': 'committed-today',
                        'commit': {
                            'author': {'date': '2026-07-28T08:00:00Z'},
                            'committer': {'date': '2026-07-28T08:00:00Z'},
                        },
                    },
                    {
                        'sha': 'committed-later',
                        'commit': {
                            'author': {'date': '2026-07-28T08:00:00Z'},
                            'committer': {'date': '2026-07-30T08:00:00Z'},
                        },
                    },
                ]]
            raise AssertionError(path)

        with patch('github_audit.gh_json', side_effect=fake_gh):
            audit.discover_pull_requests()

        self.assertEqual(['org/repo#1'], list(audit.pull_requests))
        self.assertEqual(['committed-today'], [record['sha'] for record in audit.commits.values()])


if __name__ == '__main__':
    unittest.main()
