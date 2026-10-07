"""Read T3's desktop transcript cache without credentials or server access."""
import json
import sys
from pathlib import Path


def latest_documents(records, excluded_environment_ids=()):
    """LevelDB exposes old versions; the highest sequence, even deletion, wins."""
    latest = {}
    for record in records:
        key = record.key.value
        if key not in latest or record.ldb_seq_no > latest[key].ldb_seq_no:
            latest[key] = record
    documents = {}
    for key, record in latest.items():
        if key.split(':', 1)[0] in excluded_environment_ids:
            continue
        if not record.is_live or record.value is None:
            continue
        value = record.value
        # CCL preserves V8's one-byte strings as bytes (Latin-1, not UTF-8).
        if isinstance(value, bytes):
            value = value.decode('latin-1')
        documents[key] = json.loads(value)
    return documents


def read(settings, excluded_environment_ids=()):
    if settings.get('reader_path'):
        sys.path.insert(0, str(Path(settings['reader_path']).resolve()))
    from ccl_chromium_reader.ccl_chromium_indexeddb import WrappedIndexDB
    root = Path(settings['indexeddb_directory']).expanduser()
    # The forensic reader opens files read-only, without opening a writable
    # LevelDB engine or changing the running client's lock/state.
    with WrappedIndexDB(root / 't3code_app_0.indexeddb.leveldb',
                        root / 't3code_app_0.indexeddb.blob') as wrapper:
        database = wrapper['t3code:connection-runtime']
        # Deliberately exclude catalog/auth stores and browser history.
        return {kind: latest_documents(database[kind].iterate_records(),
                                      excluded_environment_ids if kind == 'thread' else ())
                for kind in ('shell', 'thread')}


def rows_for_source(cache, environment_id, start, end):
    shell = cache['shell'].get(environment_id)
    if shell is None:
        raise ValueError('Expected environment has no cached shell snapshot')
    shell = shell['snapshot']
    projects = {p['id']: p for p in shell['projects']}
    snapshots = [v['snapshot'] for v in cache['thread'].values()
                 if v['environmentId'] == environment_id]
    from t3_history import _instant
    rows, timestamps, truncated = [], [], []
    cached_ids = set()
    for snapshot in snapshots:
        thread = snapshot['thread']
        cached_ids.add(thread['id'])
        if snapshot.get('page', {}).get('hasMore'):
            truncated.append(thread['id'])
        project = projects.get(thread['projectId'], {})
        for message in thread['messages']:
            timestamps.append(_instant(message['updatedAt']))
            created, updated = _instant(message['createdAt']), _instant(message['updatedAt'])
            if not (start <= created < end or start <= updated < end):
                continue
            rows.append({
                'thread_id': thread['id'], 'title': thread['title'], 'branch': thread.get('branch'),
                'project': project.get('title'), 'workspace_root': project.get('workspaceRoot'),
                'provider_name': (thread.get('session') or {}).get('providerName'),
                'message_id': message['id'], 'turn_id': message.get('turnId'),
                'role': message['role'], 'text': message['text'],
                'created_at': message['createdAt'], 'updated_at': message['updatedAt'],
            })
    uncached = [t for t in shell['threads'] if t['id'] not in cached_ids]
    possibly_relevant = [t['id'] for t in uncached if _instant(t['updatedAt']) >= start
                         and _instant(t['createdAt']) < end]
    coverage = {
        'listed_threads': len(shell['threads']), 'cached_threads': len(snapshots),
        'shell_updated_at': shell['updatedAt'], 'uncached_thread_ids': [t['id'] for t in uncached],
        'possibly_relevant_uncached_threads': possibly_relevant, 'truncated_thread_ids': truncated,
    }
    latest = max(timestamps).isoformat() if timestamps else None
    return latest, rows, coverage
