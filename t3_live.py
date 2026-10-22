"""Read T3 v2 HTTP snapshots using an existing Windows desktop pairing.

Credentials stay in memory and are never included in evidence or exceptions.
This adapter exposes same-origin HTTP GET and archived-inventory read RPC.
"""
import base64
import ctypes
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler


def unprotect_windows(data):
    if sys.platform != 'win32':
        raise RuntimeError('Desktop pairing credentials require Windows')

    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_ulong),
                    ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    decrypt = ctypes.WinDLL('crypt32', use_last_error=True).CryptUnprotectData
    decrypt.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                        ctypes.POINTER(Blob)]
    decrypt.restype = ctypes.c_int
    if not decrypt(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        # An S4U ("do not store password") logon has no user DPAPI credentials;
        # unlocking then depends on a cached master key and fails intermittently.
        raise RuntimeError('Windows could not unlock the existing T3 pairing '
                           f'(error 0x{ctypes.get_last_error() & 0xFFFFFFFF:08X}; '
                           'check the server task logon type)')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(output.data, 0, output.size)
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(output.data)


def desktop_catalog(settings):
    if settings.get('reader_path'):
        sys.path.insert(0, str(Path(settings['reader_path']).resolve()))
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    state = json.loads(Path(settings['local_state']).read_text(encoding='utf-8'))
    protected = base64.b64decode(state['os_crypt']['encrypted_key'])
    if not protected.startswith(b'DPAPI'):
        raise RuntimeError('Unsupported desktop key protection')
    envelope = json.loads(Path(settings['catalog']).read_text(encoding='utf-8'))
    encrypted = base64.b64decode(envelope['encryptedCatalog'])
    if not encrypted.startswith(b'v10'):
        raise RuntimeError('Unsupported desktop catalog protection')
    key = unprotect_windows(protected[5:])
    try:
        return json.loads(AESGCM(key).decrypt(encrypted[3:15], encrypted[15:], None))
    finally:
        key = None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise RuntimeError('T3 read redirect refused; credentials remain on the configured host')


class Client:
    def __init__(self, catalog, environment_id, expected_host, timeout=30):
        profiles = [p for p in catalog['profiles']
                    if p.get('environmentId') == environment_id
                    and p.get('_tag') == 'BearerConnectionProfile'
                    and urlsplit(p['httpBaseUrl']).hostname == expected_host]
        if len(profiles) != 1:
            raise RuntimeError('Expected one matching saved T3 pairing')
        profile = profiles[0]
        credentials = [c['credential'] for c in catalog['credentials']
                       if c['connectionId'] == profile['connectionId']]
        if len(credentials) != 1 or credentials[0].get('_tag') != 'BearerConnectionCredential':
            raise RuntimeError('Expected one existing T3 bearer credential')
        self.base_url = profile['httpBaseUrl'].rstrip('/')
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
            raise RuntimeError('Invalid configured T3 origin')
        self._token = credentials[0]['token']
        self.timeout = timeout
        self.opener = build_opener(NoRedirect())

    def _get(self, path):
        if not path.startswith('/api/orchestration/') or '?' in path or '#' in path:
            raise ValueError('Only fixed T3 orchestration read paths are allowed')
        request = Request(self.base_url + path, headers={
            'Authorization': 'Bearer ' + self._token,
            'x-t3-orchestration-protocol': '2',
            'Accept': 'application/json',
        }, method='GET')
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(64 * 1024 * 1024 + 1)
                if len(body) > 64 * 1024 * 1024:
                    raise RuntimeError('T3 response exceeds the bounded read limit')
                return json.loads(body)
        except HTTPError as exc:
            exc.close()
            raise RuntimeError(f'T3 authenticated read failed (HTTP {exc.code})') from None
        except (URLError, TimeoutError):
            raise RuntimeError('T3 authenticated read failed (network/timeout)') from None

    def shell(self):
        return self._get('/api/orchestration/shell')

    def thread(self, thread_id):
        return self._get('/api/orchestration/threads/' + quote(thread_id, safe=''))

    def archived(self):
        """The installed v2 server exposes archived inventory over read-only RPC."""
        import websocket
        address = self.base_url.replace('https:', 'wss:', 1).replace('http:', 'ws:', 1)
        try:
            connection = websocket.create_connection(
                address + '/ws?orchestrationProtocol=2',
                header={'Authorization': 'Bearer ' + self._token},
                timeout=self.timeout, suppress_origin=True, redirect_limit=0)
            try:
                connection.send(json.dumps({
                    '_tag': 'Request', 'id': '0',
                    'tag': 'orchestration.getArchivedShellSnapshot',
                    'payload': {}, 'headers': [],
                }))
                for _ in range(32):
                    message = json.loads(connection.recv())
                    if message.get('_tag') == 'Exit' and message.get('requestId') == '0':
                        result = message['exit']
                        if result.get('_tag') == 'Success':
                            return result['value']
                        raise RuntimeError('T3 archived inventory request failed')
                raise RuntimeError('T3 archived inventory response missing')
            finally:
                connection.close()
        except Exception:
            # WebSocket exceptions can include handshake headers.
            raise RuntimeError('T3 archived inventory unavailable') from None


def collect(client, environment_id, start, end):
    """Fetch full histories for candidate dates, including archived threads."""
    from t3_history import _instant
    shell = client.shell()
    coverage = {'archived_verified': False, 'failed_thread_ids': [],
                'requested_threads': 0, 'complete': False}
    try:
        archived = client.archived()
        coverage['archived_verified'] = True
    except RuntimeError:
        archived = {'threads': [], 'projects': []}
    shell = dict(shell)
    shell['archivedThreads'] = archived['threads']
    shell['projects'] = list({p['id']: p for p in [
        *shell['projects'], *archived.get('projects', [])]}.values())
    inventory = {t['id']: t for t in [*shell['threads'], *archived['threads']]}
    candidates = [t for t in inventory.values()
                  if _instant(t['createdAt']) < end and _instant(t['updatedAt']) >= start]
    coverage['requested_threads'] = len(candidates)
    cache = {'shell': {environment_id: {'snapshot': shell}}, 'thread': {}}
    for thread in candidates:
        try:
            snapshot = client.thread(thread['id'])
            # /threads/:id returns a full projection, not the bounded window.
            if 'projection' not in snapshot or 'messages' not in snapshot['projection']:
                raise RuntimeError('Invalid T3 full-thread snapshot')
            cache['thread'][environment_id + ':' + thread['id']] = {
                'environmentId': environment_id, 'snapshot': snapshot}
        except (RuntimeError, ValueError, KeyError):
            coverage['failed_thread_ids'].append(thread['id'])
    coverage['complete'] = coverage['archived_verified'] and not coverage['failed_thread_ids']
    return cache, coverage
