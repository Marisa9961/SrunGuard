from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Event, Thread
import unittest

from srun_guard.network import (Cancelled, NetworkError, Response, SrunClient,
                                Transport, page_ip)
from srun_guard.protocol import ProtocolError
from srun_guard.settings import Settings


class FakeTransport:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def get(self, url, params=None, limit=262144):
        self.calls.append((url, params))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict):
            return Response(200, json.dumps(reply).encode())
        return reply


class NetworkTests(unittest.TestCase):
    def client(self, replies):
        transport = FakeTransport(replies)
        return SrunClient(Settings(username="test", portal="https://portal.example.org"),
                          "secret", Event(), transport)

    def test_unconfigured_client_never_creates_transport(self):
        from unittest.mock import patch
        with patch("srun_guard.network.Transport") as transport:
            with self.assertRaises(ValueError):
                SrunClient(Settings(), "secret", Event())
            with self.assertRaises(ValueError):
                SrunClient(Settings(username="test", portal="https://portal.example.org"), "", Event())
            transport.assert_not_called()

    def test_ip_formats(self):
        for text in ('ip     : "10.1.2.3"', "var online_ip='10.1.2.3';",
                     '<input id="user_ip" value="10.1.2.3">'):
            self.assertEqual(page_ip(text), "10.1.2.3")
        self.assertEqual(page_ip('ip: "999.0.0.1"'), "")
        self.assertEqual(page_ip('ip: "0.0.0.0"'), "")

    def test_login_sequence_and_server_ip(self):
        client = self.client([
            Response(200, b'ip : "10.0.0.1"'),
            {"challenge": "a" * 64, "client_ip": "10.0.0.2"}, {"error": "ok"},
        ])
        self.assertEqual(client.login(), "10.0.0.2")
        self.assertEqual(client.http.calls[1][1]["ip"], "10.0.0.1")
        self.assertEqual(client.http.calls[2][1]["ip"], "10.0.0.2")
        self.assertNotEqual(client.http.calls[1][1]["callback"], client.http.calls[2][1]["callback"])
        self.assertNotIn("secret", str(client.http.calls))

    def test_diagnostic_steps_never_include_credentials_or_raw_responses(self):
        client = self.client([
            Response(200, b'ip : "10.0.0.1"'),
            {"challenge": "a" * 64, "client_ip": "10.0.0.2"}, {"error": "ok"},
        ])
        events = []
        client.diagnostic = lambda key, message: events.append((key, message))
        client.login()
        self.assertEqual([key for key, _ in events],
                         ["login.page", "login.challenge", "login.submit", "login.accepted"])
        text = " ".join(message for _, message in events)
        self.assertTrue(text.isascii())
        for private in ("secret", "10.0.0.1", "10.0.0.2", "a" * 64, "https://portal.example.org"):
            self.assertNotIn(private, text)

    def test_challenge_ip_fallback(self):
        client = self.client([Response(200, b"<html></html>"),
                              {"challenge": "b" * 64, "client_ip": "10.0.0.3"},
                              {"suc_msg": "ip_already_online_error"}])
        self.assertEqual(client.login(), "10.0.0.3")

    def test_malformed_challenge_never_submits_login(self):
        for challenge in ({}, {"challenge": "not-a-token"}, {"challenge": "a" * 64}):
            client = self.client([Response(200, b""), challenge])
            with self.assertRaises(ProtocolError):
                client.login()
            self.assertEqual(len(client.http.calls), 2)

    def test_rejected_login_and_server_text_not_logged(self):
        client = self.client([Response(200, b'ip:"10.1.2.3"'), {"challenge": "a" * 64},
                              {"error": "secret appears in this malicious reply"}])
        with self.assertRaises(ProtocolError) as error:
            client.login()
        self.assertNotIn("secret", str(error.exception))

    def test_captive_portal_is_offline(self):
        client = self.client([Response(200, b"<html>login</html>")] * 3)
        self.assertFalse(client.online())

    def test_probe_failover(self):
        client = self.client([NetworkError("failed"), Response(200, b"Microsoft Connect Test")])
        self.assertTrue(client.online())
        self.assertEqual(len(client.http.calls), 2)
        self.assertTrue(self.client([Response(204, b"")]).online())

    def test_cancel_before_http(self):
        client = self.client([])
        client.stop.set()
        with self.assertRaises(Cancelled):
            client.online()
        with self.assertRaises(Cancelled):
            client.login()
        self.assertEqual(client.http.calls, [])


class Handler(BaseHTTPRequestHandler):
    paths = []

    def do_GET(self):
        type(self).paths.append(self.path)
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/must-not-follow")
            self.end_headers()
        else:
            body = b"ok" if self.path == "/ok" else b"x" * 100
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_real_http(self):
        self.assertEqual(Transport(2).get(self.base + "/ok"), Response(200, b"ok"))

    def test_response_limit(self):
        with self.assertRaisesRegex(NetworkError, "Response too large"):
            Transport(2).get(self.base + "/large", limit=10)

    def test_redirect_refused_without_url_in_error(self):
        with self.assertRaises(NetworkError) as error:
            Transport(2).get(self.base + "/redirect", {"password": "secret"})
        self.assertNotIn("secret", str(error.exception))
        self.assertNotIn("/must-not-follow", Handler.paths)


if __name__ == "__main__":
    unittest.main()
