import asyncio
import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from djjev.jev_client import JevClient, ClientError

REQUEST = {'model': 'jev-latest', 'state': {'playing': True},
           'questions': {'action': {'type': 'choice', 'criteria': {'hold': 'Keep playing'}}}}
BODY = {'model': 'jev-fixture', 'answers': {'action': {'choice': 'hold'}}}


class Response:
    def __init__(self, status=200, body=None, retry=None):
        self.status, self.body, self.retry = status, body if body is not None else json.dumps(BODY).encode(), retry
    def getheader(self, name): return self.retry
    def read(self, count): return self.body
    def close(self): pass


class Connection:
    def __init__(self, responses):
        self.responses = responses
        self.requests = []
        self.sock = None
        self.closed = False
    def request(self, *args, **kwargs): self.requests.append((args, kwargs))
    def getresponse(self): return self.responses.pop(0)
    def close(self): self.closed = True


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_connection_and_key_only_in_header(self):
        connection = Connection([Response(), Response()]); factories = []
        def factory(*args, **kwargs):
            factories.append((args, kwargs)); return connection
        client = JevClient('private-fixture-key', connection_factory=factory)
        for _ in range(2):
            result = await client.ask(REQUEST)
            self.assertIn('request_seconds', result)
            self.assertNotIn('private-fixture-key', json.dumps(result))
        self.assertEqual(len(factories), 1)
        for args, kwargs in connection.requests:
            self.assertEqual(args[:2], ('POST', '/v1/systemone'))
            self.assertNotIn(b'private-fixture-key', kwargs['body'])
            self.assertEqual(kwargs['headers']['Authorization'], 'Bearer private-fixture-key')
        self.assertNotIn('private-fixture-key', repr(client))
        await client.close()

    async def test_rate_limits_use_exponential_backoff(self):
        connection = Connection([Response(429), Response(529), Response()]); sleeps = []
        async def sleep(seconds): sleeps.append(seconds)
        client = JevClient('private-fixture-key', connection_factory=lambda *a, **k: connection, sleeper=sleep)
        result = await client.ask(REQUEST)
        self.assertEqual(sleeps, [.25, .5])
        self.assertEqual(len(connection.requests), 3)
        self.assertEqual(result['answers'], BODY['answers'])
        await client.close()

    async def test_error_body_cannot_leak_key_and_is_not_replaced_with_answer(self):
        connection = Connection([Response(401, b'private-fixture-key debug body')])
        client = JevClient('private-fixture-key', connection_factory=lambda *a, **k: connection)
        with self.assertRaises(ClientError) as failed:
            await client.ask(REQUEST)
        self.assertNotIn('private-fixture-key', str(failed.exception))
        self.assertIn('401', str(failed.exception))
        self.assertEqual(len(connection.requests), 1)
        await client.close()

    async def test_invalid_response_or_wrong_questions_are_rejected(self):
        for body in (b'not-json', b'{"model":"fixture","answers":{"other":{}}}'):
            connection = Connection([Response(body=body)])
            client = JevClient('private-fixture-key', connection_factory=lambda *a, **k: connection)
            with self.assertRaises(ClientError):
                await client.ask(REQUEST)
            await client.close()

    async def test_concurrent_ask_calls_still_use_one_connection_at_a_time(self):
        class Delayed(Connection):
            active = 0
            maximum = 0
            def getresponse(self):
                self.active += 1; self.maximum = max(self.maximum, self.active)
                time.sleep(.02)
                self.active -= 1
                return super().getresponse()
        connection = Delayed([Response(), Response()])
        client = JevClient('private-fixture-key', connection_factory=lambda *a, **k: connection)
        await asyncio.gather(client.ask(REQUEST), client.ask(REQUEST))
        self.assertEqual(connection.maximum, 1)
        await client.close()

    async def test_request_timeout_allows_fresh_next_request_without_overlapping_posts(self):
        class Slow(Connection):
            def getresponse(self):
                time.sleep(.075)
                return super().getresponse()
        first, second = Slow([Response()]), Connection([Response()])
        connections = iter((first, second))
        client = JevClient('private-fixture-key', timeout=.05,
                           connection_factory=lambda *a, **k: next(connections))
        with self.assertRaises(ClientError):
            await client.ask(REQUEST)
        self.assertFalse(client._closed)
        self.assertTrue(first.closed)
        result = await client.ask(REQUEST)
        self.assertEqual(result['answers'], BODY['answers'])
        self.assertEqual(len(first.requests), 1)
        self.assertEqual(len(second.requests), 1)
        await client.close()


if __name__ == '__main__':
    unittest.main()
