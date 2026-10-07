"""Read T3 conversation projections as semantic evidence, never elapsed work time.

Each configured database represents one server or a consistent SQLite backup of
that server. Pairing a client is not proof that remote transcripts are local.
"""
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def _instant(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('T3 timestamp has no timezone')
    return value


def _read(database, start, end):
    path = Path(os.path.expandvars(database)).expanduser().resolve()
    # mode=ro prevents creating an empty database for a misspelled source path.
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        latest = db.execute('SELECT MAX(updated_at) FROM projection_thread_messages').fetchone()[0]
        rows = db.execute('''
            SELECT m.*, t.title, t.branch, p.title AS project,
                   p.workspace_root, s.provider_name
            FROM projection_thread_messages m
            JOIN projection_threads t ON t.thread_id = m.thread_id
            LEFT JOIN projection_projects p ON p.project_id = t.project_id
            LEFT JOIN projection_thread_sessions s ON s.thread_id = t.thread_id
            WHERE (julianday(m.created_at) >= julianday(?) AND julianday(m.created_at) < julianday(?))
               OR (julianday(m.updated_at) >= julianday(?) AND julianday(m.updated_at) < julianday(?))
            ORDER BY m.created_at, m.message_id
        ''', (start.isoformat(), end.isoformat()) * 2).fetchall()
        return latest, [dict(row) for row in rows]


def collect(sources, start, end, client_cache=None):
    """Collect a half-open local day, keeping unavailable servers explicit.

    Message IDs plus thread ID identify migrated copies. Conflicting copies are
    retained and warned about; identical titles alone never merge conversations.
    Current provider/branch metadata may differ from historical turn metadata.
    """
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError('Expected an ordered pair of timezone-aware day boundaries')
    result = {'chats': [], 'sources': [], 'warnings': [], 'duplicate_messages_removed': 0,
              'collection_stats': {'database_reads': 0, 'client_cache_reads': 0}}
    sources = list(sources)
    database_reads = {}
    authoritative_environments = set()
    for source in sources:
        if not source.get('database'):
            continue
        key = os.path.normcase(str(Path(os.path.expandvars(source['database'])).expanduser().resolve()))
        if key not in database_reads:
            result['collection_stats']['database_reads'] += 1
            try:
                database_reads[key] = _read(source['database'], start, end)
            except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
                database_reads[key] = exc
        if not isinstance(database_reads[key], Exception) and source.get('environment_id'):
            authoritative_environments.add(source['environment_id'])
    cache = None
    if client_cache:
        try:
            import t3_client_cache
            result['collection_stats']['client_cache_reads'] += 1
            cache = t3_client_cache.read(client_cache, authoritative_environments)
            known = {s.get('environment_id') for s in sources}
            for environment_id in cache['shell']:
                if environment_id not in known:
                    sources.append({'name': f'T3 cached environment {environment_id}', 'environment_id': environment_id})
        except Exception as exc:
            # Optional cache corruption/unavailability must not suppress the
            # independent server database. No record content goes into errors.
            result['warnings'].append(f'T3 client cache unavailable ({type(exc).__name__}); check reader dependency and cache integrity')
    chats = {}
    seen = {}
    for source in sources:
        name = source['name']
        coverage = {'name': name, 'status': 'unavailable', 'latest_message_at': None,
                    'requested_day_messages': 0}
        result['sources'].append(coverage)
        from_cache = not source.get('database') and cache is not None and source.get('environment_id')
        if not source.get('database') and not from_cache:
            result['warnings'].append(f'{name}: no readable transcript database/export configured; coverage unknown')
            continue
        try:
            if from_cache:
                latest, rows, cache_coverage = t3_client_cache.rows_for_source(cache, source['environment_id'], start, end)
                coverage.update(cache_coverage)
            else:
                key = os.path.normcase(str(Path(os.path.expandvars(source['database'])).expanduser().resolve()))
                acquired = database_reads[key]
                if isinstance(acquired, Exception):
                    raise acquired
                latest, rows = acquired
            # Validate before incorporating this source, so schema/time errors
            # cannot leave a partly ingested server marked as fully available.
            for row in rows:
                _instant(row['created_at'])
                _instant(row['updated_at'])
            if latest:
                _instant(latest)
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError) as exc:
            result['warnings'].append(f'{name}: transcript read failed ({type(exc).__name__}: {exc})')
            continue
        coverage.update(status='cached_partial' if from_cache else 'readable', latest_message_at=latest, requested_day_messages=len(rows))
        if from_cache:
            result['warnings'].append(
                f'{name}: cached transcripts only ({coverage["cached_threads"]}/{coverage["listed_threads"]} listed threads); '
                f'{len(coverage["possibly_relevant_uncached_threads"])} uncached threads may intersect this day; '
                f'{len(coverage["truncated_thread_ids"])} paginated bodies; freshness/archived coverage unverified')
        if latest is None or _instant(latest) < start:
            result['warnings'].append(f'{name}: latest transcript predates requested day ({latest}); inactivity or stale export is unverified')
        for row in rows:
            thread = row['thread_id']
            chat = chats.setdefault(thread, {
                'thread_id': thread, 'title': row['title'], 'project': row['project'],
                'workspace': row['workspace_root'], 'branch': row['branch'],
                'sources': set(), 'providers': set(), 'client_device': 'unknown',
                'tickets': set(), 'messages': [],
            })
            chat['sources'].add(name)
            if row['provider_name']:
                chat['providers'].add(row['provider_name'])
            context = f"{row['title']} {row['branch'] or ''}"
            if row['role'] == 'user':
                context += ' ' + row['text']
            chat['tickets'].update(re.findall(r'\b(?:ITEM|ROMSD)-\d+\b', context))
            identity = (thread, row['message_id'])
            signature = (row['role'], row['text'], _instant(row['created_at']), _instant(row['updated_at']))
            previous = seen.setdefault(identity, {})
            if signature in previous:
                previous[signature]['sources'].append(name)
                result['duplicate_messages_removed'] += 1
                continue
            if previous:
                result['warnings'].append(f'{name}: conflicting copies of message {row["message_id"]}; retained for reconciliation')
            message = {key: row[key] for key in ('message_id', 'turn_id', 'role', 'text', 'created_at', 'updated_at')}
            message.update(sources=[name], continued_from_previous_day=_instant(row['created_at']) < start,
                           updated_after_requested_day=_instant(row['updated_at']) >= end)
            if message['updated_after_requested_day']:
                result['warnings'].append(f'{name}: message {row["message_id"]} was revised after the requested day; text is not a historical snapshot')
            previous[signature] = message
            chat['messages'].append(message)
    for chat in chats.values():
        for key in ('sources', 'providers', 'tickets'):
            chat[key] = sorted(chat[key])
        chat['messages'].sort(key=lambda m: (_instant(m['created_at']), m['message_id']))
        chat['message_count'] = len(chat['messages'])
        chat['user_message_count'] = sum(m['role'] == 'user' and not m['continued_from_previous_day'] for m in chat['messages'])
        result['chats'].append(chat)
    result['chats'].sort(key=lambda c: (c['title'], c['thread_id']))
    return result


def print_summary(result, local_timezone=timezone.utc):
    print('\nT3 CONVERSATION EVIDENCE (server provenance; client device unknown)')
    print('Prompts/outcomes are attribution evidence, not work durations. Native Codex history is not collected in T3 mode.')
    stats = result.get('collection_stats', {})
    print(f'Collection: {stats.get("database_reads", 0)} unique database reads; {stats.get("client_cache_reads", 0)} client-cache scan(s).')
    for source in result['sources']:
        latest = source['latest_message_at']
        if latest:
            latest = _instant(latest).astimezone(local_timezone).isoformat()
        print(f"  {source['name']}: {source['status']}; day messages={source['requested_day_messages']}; latest={latest}")
    for warning in result['warnings']:
        print(f'  WARNING: {warning}')
    for chat in result['chats']:
        print(f"\n- {chat['title']} [{chat['thread_id']}] | servers: {', '.join(chat['sources'])}")
        print(f"  project={chat['project']}; branch={chat['branch']}; current provider={','.join(chat['providers'])}; tickets={','.join(chat['tickets'])}")
        print(f"  {chat['user_message_count']} user-role messages (may include delegated prompts); {chat['message_count']} total messages")
        prompts = [m for m in chat['messages'] if m['role'] == 'user']
        outcomes = [m for m in chat['messages'] if m['role'] == 'assistant'][-2:]
        for message in prompts + outcomes:
            timestamp = _instant(message['created_at']).astimezone(local_timezone).strftime('%H:%M:%S')
            excerpt = ' '.join(message['text'].split())[:500]
            print(f"  {timestamp} {message['role']}: {excerpt}")
