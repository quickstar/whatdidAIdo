import unittest

import moco_sync


CONFIG = {
    'moco': {
        'base_url': 'https://example.mocoapp.com',
        'jira_base_url': 'https://example.atlassian.net/browse',
        'customer_aliases': {'Fernfachhochschule Schweiz FFHS': 'FFHS'},
        'customer_projects': {
            '2026': {
                'FFHS': {
                    'project_id': '100',
                    'project_name': 'W&S 2026 - FFHS',
                    'task_id': '200',
                    'task_name': 'Entwicklung',
                }
            }
        },
    }
}


def worklog(description='Fix payment behavior', hours=4.5):
    return {
        'date': '2026-07-28',
        'activities': [{
            'ticket': 'ROMSD-1',
            'customer': 'Fernfachhochschule Schweiz FFHS',
            'description': description,
            'hours': hours,
            'billable': False,
        }],
    }


class FakeClient:
    def __init__(self, activities=None):
        self.records = list(activities or [])
        self.created = []
        self.updated = []

    def activities(self, activity_date):
        return [record.copy() for record in self.records if record['date'] == activity_date]

    def create_activity(self, payload):
        record = {
            **moco_sync.public_payload(payload),
            'id': str(len(self.records) + 1),
            'project': {'id': str(payload['project_id']), 'name': payload.get('_project_name')},
            'task': {'id': str(payload['task_id']), 'name': payload.get('_task_name')},
            'remoteUrl': payload.get('remote_url') or '',
            'isBillable': payload['billable'],
            'workedSeconds': payload['seconds'],
        }
        self.records.append(record)
        self.created.append(record)
        return record

    def update_activity(self, activity_id, payload):
        self.updated.append((activity_id, payload.copy()))
        self.records = [record for record in self.records if record.get('id') != activity_id]
        record = self.create_activity(payload)
        record['id'] = activity_id
        return record


class MocoSyncTests(unittest.TestCase):
    def test_dry_run_resolves_mapping_without_writing(self):
        client = FakeClient()
        result = moco_sync.synchronize(client, worklog(), CONFIG)
        self.assertEqual('would-create', result['results'][0]['action'])
        self.assertEqual([], client.created)

    def test_apply_is_idempotent_and_sets_native_jira_link(self):
        client = FakeClient()
        first = moco_sync.synchronize(client, worklog(), CONFIG, apply=True)
        second = moco_sync.synchronize(client, worklog(), CONFIG, apply=True)
        self.assertEqual('created', first['results'][0]['action'])
        self.assertEqual('unchanged', second['results'][0]['action'])
        self.assertEqual(1, len(client.created))
        self.assertEqual('jira', client.records[0]['remote_service'])
        self.assertEqual('ROMSD-1', client.records[0]['tag'])
        self.assertEqual('https://example.atlassian.net/browse/ROMSD-1', client.records[0]['remote_url'])

    def test_differing_existing_activity_is_preserved_without_explicit_update(self):
        client = FakeClient()
        moco_sync.synchronize(client, worklog('Manual description'), CONFIG, apply=True)
        result = moco_sync.synchronize(client, worklog('Replacement'), CONFIG, apply=True)
        self.assertEqual('preserved-existing', result['results'][0]['action'])
        self.assertEqual('Manual description', client.records[0]['description'])

    def test_explicit_update_repairs_existing_activity(self):
        client = FakeClient()
        moco_sync.synchronize(client, worklog('Manual description'), CONFIG, apply=True)
        result = moco_sync.synchronize(
            client,
            worklog('Replacement'),
            CONFIG,
            apply=True,
            update_existing=True,
        )
        self.assertEqual('updated', result['results'][0]['action'])
        self.assertEqual('Replacement', client.records[0]['description'])
        self.assertEqual(1, len(client.updated))

    def test_sub_fifteen_minute_activity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'at least 900 seconds'):
            moco_sync.synchronize(FakeClient(), worklog(hours=0.1), CONFIG)

    def test_non_ticket_activity_uses_stable_sync_key_without_remote_link(self):
        client = FakeClient()
        meeting = {
            'date': '2026-07-28',
            'activities': [{
                'sync_key': 'acme-refinement',
                'customer': 'FFHS',
                'description': 'Refinement',
                'hours': 1,
                'billable': True,
            }],
        }
        first = moco_sync.synchronize(client, meeting, CONFIG, apply=True)
        second = moco_sync.synchronize(client, meeting, CONFIG, apply=True)
        self.assertEqual('created', first['results'][0]['action'])
        self.assertEqual('unchanged', second['results'][0]['action'])
        self.assertNotIn('remote_service', client.records[0])
        self.assertEqual('', client.records[0]['tag'])

    def test_legacy_non_ticket_sync_key_tag_is_removed_on_explicit_update(self):
        legacy = {
            'id': '42',
            'date': '2026-07-28',
            'project': {'id': '100'},
            'task': {'id': '200'},
            'seconds': 3600,
            'description': 'Refinement',
            'billable': True,
            'tag': 'acme-refinement',
        }
        meeting = {
            'date': '2026-07-28',
            'activities': [{
                'sync_key': 'acme-refinement',
                'customer': 'FFHS',
                'description': 'Refinement',
                'hours': 1,
                'billable': True,
            }],
        }
        client = FakeClient([legacy])
        dry_run = moco_sync.synchronize(client, meeting, CONFIG)
        updated = moco_sync.synchronize(client, meeting, CONFIG, apply=True, update_existing=True)
        self.assertEqual('preserved-existing', dry_run['results'][0]['action'])
        self.assertEqual({'current': 'acme-refinement', 'desired': ''}, dry_run['results'][0]['differences']['tag'])
        self.assertEqual('updated', updated['results'][0]['action'])
        self.assertEqual('', client.records[0]['tag'])

    def test_non_ticket_natural_identity_survives_duration_change_without_tag(self):
        meeting = {
            'date': '2026-07-28',
            'activities': [{
                'sync_key': 'acme-refinement',
                'customer': 'FFHS',
                'description': 'Refinement',
                'hours': 1,
                'billable': True,
            }],
        }
        client = FakeClient()
        moco_sync.synchronize(client, meeting, CONFIG, apply=True)
        meeting['activities'][0]['hours'] = 1.25
        result = moco_sync.synchronize(client, meeting, CONFIG)
        self.assertEqual('preserved-existing', result['results'][0]['action'])
        self.assertEqual({'current': 3600, 'desired': 4500}, result['results'][0]['differences']['seconds'])

    def test_non_ticket_natural_identity_survives_manual_project_change(self):
        moved = {
            'id': '42',
            'date': '2026-07-28',
            'project': {'id': '999'},
            'task': {'id': '888'},
            'seconds': 3600,
            'description': 'Refinement',
            'billable': True,
            'tag': '',
        }
        meeting = {
            'date': '2026-07-28',
            'activities': [{
                'sync_key': 'acme-refinement',
                'customer': 'FFHS',
                'description': 'Refinement',
                'hours': 1,
                'billable': True,
            }],
        }
        result = moco_sync.synchronize(FakeClient([moved]), meeting, CONFIG)
        self.assertEqual('preserved-existing', result['results'][0]['action'])
        self.assertEqual({'current': 999, 'desired': 100}, result['results'][0]['differences']['project_id'])


if __name__ == '__main__':
    unittest.main()
