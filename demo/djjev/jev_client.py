"""Persistent asynchronous TypeSafe client; key comes only from its constructor."""
import asyncio
import http.client
import json
import socket
import threading
import time

ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
MODEL = 'jev-latest'


class ClientError(RuntimeError):
    pass


class JevClient:
    def __init__(self, key, *, timeout=8., connection_factory=http.client.HTTPSConnection,
                 clock=time.monotonic, sleeper=asyncio.sleep):
        if not isinstance(key, str) or not key or any(ord(c) < 32 for c in key):
            raise ValueError('Existing API credential is unavailable.')
        self._key = key
        self.timeout = min(8., max(.05, timeout))
        self._factory, self._clock, self._sleep = connection_factory, clock, sleeper
        self._connection = None
        self._lock = asyncio.Lock()
        self._thread_lock = threading.Lock()
        self._closed = False
        self._generation = 0

    def _drop(self):
        connection, self._connection = self._connection, None
        if connection:
            try:
                if getattr(connection, 'sock', None):
                    connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass

    def _post(self, body, deadline, generation):
        with self._thread_lock:
            timeout = deadline-self._clock()
            if self._closed or generation != self._generation or timeout <= 0:
                raise ClientError('Jev client stopped.')
            try:
                if self._connection is None:
                    self._connection = self._factory('api.typesafe.ai', 443, timeout=timeout)
                connection = self._connection
                if self._closed or generation != self._generation or self._clock() >= deadline:
                    self._drop()
                    raise ClientError('TypeSafe request expired before dispatch.')
                connection.timeout = timeout
                if getattr(connection, 'sock', None):
                    connection.sock.settimeout(timeout)
                connection.request('POST', '/v1/systemone', body=body,
                                   headers={'Authorization': 'Bearer '+self._key, 'Content-Type': 'application/json'})
                response = connection.getresponse()
                status = response.status
                retry_after = response.getheader('Retry-After')
                data = response.read(1_000_001)
                response.close()
                if len(data) > 1_000_000:
                    self._drop()
                    raise ClientError('TypeSafe response exceeded the size limit.')
                return status, retry_after, data
            except ClientError:
                raise
            except (OSError, http.client.HTTPException):
                self._drop()
                raise ClientError('TypeSafe connection interrupted; no answer was substituted.') from None

    async def ask(self, request):
        if (not isinstance(request, dict) or request.get('model') != MODEL
                or 'state' not in request or not request.get('questions')):
            raise ClientError('Invalid Jev request.')
        # Do not send private local helper fields or transport settings.
        payload = {k: request[k] for k in ('model', 'state', 'questions')}
        try:
            body = json.dumps(payload, allow_nan=False).encode()
        except (TypeError, ValueError):
            raise ClientError('Invalid Jev request data.') from None
        async with self._lock:
            if self._closed:
                raise ClientError('Jev client stopped.')
            started = self._clock()
            self._generation += 1
            generation = self._generation
            try:
                for attempt in range(3):
                    remaining = self.timeout-(self._clock()-started)
                    if remaining <= 0:
                        raise ClientError('TypeSafe answer deadline exceeded.')
                    status, retry_header, data = await asyncio.wait_for(
                        asyncio.to_thread(self._post, body, started+self.timeout, generation), timeout=remaining)
                    if status in (429, 529) and attempt < 2:
                        delay = .25*(2**attempt)
                        try:
                            delay = max(delay, min(2., float(retry_header)))
                        except (TypeError, ValueError):
                            pass
                        if self._clock()-started+delay >= self.timeout:
                            raise ClientError('TypeSafe rate-limit deadline exceeded.')
                        await self._sleep(delay)
                        continue
                    if status < 200 or status >= 300:
                        raise ClientError(f'TypeSafe HTTP {status}; no answer was substituted.')
                    try:
                        response = json.loads(data)
                    except (ValueError, UnicodeError):
                        raise ClientError('TypeSafe returned invalid JSON.') from None
                    if (not isinstance(response, dict) or not isinstance(response.get('model'), str)
                            or not isinstance(response.get('answers'), dict)
                            or set(response['answers']) != set(payload['questions'])):
                        raise ClientError('TypeSafe response does not match the requested questions.')
                    return {**response, 'request_seconds': self._clock()-started}
                raise ClientError('TypeSafe is temporarily unavailable.')
            except asyncio.CancelledError:
                # A cancelled to_thread call may still be in the socket. Shut it
                # down and forbid another request using that same connection.
                await self.close()
                raise
            except TimeoutError:
                self._generation += 1
                self._drop()
                raise ClientError('TypeSafe answer deadline exceeded.') from None

    async def close(self):
        self._closed = True
        self._drop()

    def __repr__(self):
        return 'JevClient(credential=<private>)'
