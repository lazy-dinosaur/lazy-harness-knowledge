"""The Jev host is what the core resolves for judgement transport, and an installed transport overrides it."""
import unittest
from unittest.mock import patch

import host_defaults
import jev_http


class JevHost(unittest.TestCase):
    def tearDown(self):
        host_defaults.install_judgement_http(None)

    def test_default_resolves_to_the_jev_host(self):
        self.assertIs(host_defaults.judgement_http(), jev_http.send)

    def test_installed_transport_wins(self):
        fake = lambda request, timeout=60: None
        host_defaults.install_judgement_http(fake)
        self.assertIs(host_defaults.judgement_http(), fake)

    def test_send_delegates_to_urlopen_with_timeout(self):
        with patch.object(jev_http, 'urlopen', return_value='resp') as u:
            self.assertEqual(jev_http.send('req', timeout=7), 'resp')
        u.assert_called_once_with('req', timeout=7)
