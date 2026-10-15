from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from clover_mail.content import RemoteImageCandidate
from clover_mail.remote_images import _download, _public_address


class RemoteImageTests(unittest.TestCase):
    def test_selected_https_image_is_bounded_and_has_no_tracking_headers(self):
        data = (b"\x89PNG\r\n\x1a\n" + b"\0" * 8 + (800).to_bytes(4, "big")
                + (1100).to_bytes(4, "big") + b"x" * 12_000)

        class FakeResponse:
            status = 200
            def getheader(self, name):
                return {"Content-Type": "image/png", "Content-Length": str(len(data))}.get(name)
            def read(self, size):
                assert size > len(data)
                return data

        class FakeConnection:
            def __init__(self):
                self.headers = None
            def request(self, method, path, headers):
                self.headers = headers
                assert method == "GET"
                assert path == "/poster.png?unique=123"
            def getresponse(self):
                return FakeResponse()
            def close(self):
                pass

        connection = FakeConnection()
        with patch("clover_mail.remote_images._public_address", return_value="8.8.8.8"), \
             patch("clover_mail.remote_images._PinnedHTTPSConnection", return_value=connection):
            image = _download(RemoteImageCandidate("https://cdn.example.org/poster.png?unique=123",
                                                   "Event poster", 3), 100_000)
        self.assertEqual((image.width, image.height), (800, 1100))
        self.assertNotIn("Cookie", connection.headers)
        self.assertNotIn("Referer", connection.headers)

    def test_private_and_mixed_dns_results_are_rejected(self):
        with patch("clover_mail.remote_images.socket.getaddrinfo", return_value=[
            (0, 0, 0, "", ("8.8.8.8", 443)),
            (0, 0, 0, "", ("127.0.0.1", 443)),
        ]):
            self.assertIsNone(_public_address("example.org"))

    def test_http_and_nonstandard_ports_do_not_start_dns_lookup(self):
        with patch("clover_mail.remote_images._public_address") as lookup:
            self.assertIsNone(_download(RemoteImageCandidate("http://example.org/poster.png", "poster", 3), 100000))
            self.assertIsNone(_download(RemoteImageCandidate("https://example.org:8080/poster.png", "poster", 3), 100000))
            lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
