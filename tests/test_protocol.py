import hashlib
import hmac
import json
import unittest
from unittest.mock import patch

from srun_guard.protocol import ProtocolError, encode_info, login_parameters, parse_reply


TOKEN = "0123456789abcdef0123456789abcdef"


class ProtocolTests(unittest.TestCase):
    def test_wire_vectors(self):
        # Fixed compatibility fixtures; tests and application do not import example/.
        for data, expected in (
            (b"", "{SRBX1}"),
            (b"a", "{SRBX1}ozirbATTtMD="),
            (b"abcd", "{SRBX1}8mldMaXaIpH="),
            (b"Hello Srun!", "{SRBX1}eseb2nQ06ntY5TRi2ULqqv=="),
            (b'{"username":"test","password":"a b"}',
             "{SRBX1}2M/YsWr+VwxI/xgYEzctN2OgIo2IudlSMyxwqf7meUcnyd6LdMqsvv=="),
        ):
            with self.subTest(data=data):
                self.assertEqual(encode_info(data, TOKEN), expected)

    def test_json_and_jsonp(self):
        self.assertEqual(parse_reply('{"error":"ok"}'), {"error": "ok"})
        self.assertEqual(parse_reply(' cb_123 ( {"error": "ok"} ); ', "cb_123"), {"error": "ok"})
        for invalid in ('cb({});alert(1)', 'cb([])', '[]', '<html>login</html>', 'cb({bad})'):
            with self.subTest(invalid=invalid), self.assertRaises(ProtocolError):
                parse_reply(invalid)
        with self.assertRaises(ProtocolError):
            parse_reply('other({})', "expected")

    def test_empty_and_password_hmac_modes(self):
        for use_password in (False, True):
            params = login_parameters("test", "secret", "10.1.2.3", "0", TOKEN, use_password)
            md5 = hmac.new(TOKEN.encode(), b"secret" if use_password else b"", hashlib.md5).hexdigest()
            self.assertEqual(params["password"], "{MD5}" + md5)
            expected = hashlib.sha1("".join(TOKEN + v for v in
                ("test", md5, "0", "10.1.2.3", "200", "1", params["info"])).encode()).hexdigest()
            self.assertEqual(params["chksum"], expected)

    def test_password_spaces_quotes_and_unicode_preserved(self):
        password = 'a b"\\中文'
        with patch("srun_guard.protocol.encode_info", return_value="{SRBX1}fixture") as encode:
            login_parameters("test", password, "10.1.2.3", "0", TOKEN)
        document = json.loads(encode.call_args.args[0])
        self.assertEqual(document["password"], password)
        self.assertNotIn(password, str(login_parameters("test", password, "10.1.2.3", "0", TOKEN)))


if __name__ == "__main__":
    unittest.main()
