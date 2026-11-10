"""The embedding host is what the core resolves, an installed transport overrides it, and the core validates results."""
import asyncio, unittest
from unittest.mock import patch

import host_defaults
import embed_http
import embedding_transport as et


class EmbeddingHost(unittest.TestCase):
    def tearDown(self):
        host_defaults.install_embedding_transport()

    def test_defaults_resolve_to_the_embedding_host(self):
        self.assertIs(host_defaults.embedding_http(), embed_http.send)
        self.assertIs(host_defaults.embedding_remote_post(), embed_http.remote_post)

    def test_installed_transport_wins_and_core_still_validates(self):
        async def fake_post(url, headers, body, timeout):
            return {'model': body['model'], 'data': [{'index': 0, 'embedding': [0.0] * 1024}]}
        host_defaults.install_embedding_transport(remote=fake_post)
        value = asyncio.run(et.send('u', {}, {'model': 'm', 'input': ['x']}, 0, 'a'))
        self.assertEqual(len(value['data'][0]['embedding']), 1024)
        async def wrong_shape(url, headers, body, timeout):
            return {'model': body['model'], 'data': [{'index': 0, 'embedding': [0.0] * 3}]}
        host_defaults.install_embedding_transport(remote=wrong_shape)
        with self.assertRaisesRegex(ValueError, '1024'):
            asyncio.run(et.send('u', {}, {'model': 'm', 'input': ['x']}, 0, 'a'))

    def test_local_send_delegates_to_urlopen_with_timeout(self):
        with patch.object(embed_http, 'urlopen', return_value='resp') as u:
            self.assertEqual(embed_http.send('req', timeout=5), 'resp')
        u.assert_called_once_with('req', timeout=5)
