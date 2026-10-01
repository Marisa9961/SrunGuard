"""Direct, timeout-bound HTTP with certificate verification and no redirect login leaks."""

from dataclasses import dataclass
from http.client import HTTPException
import ipaddress
import re
import secrets
import socket
import ssl
import time
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (HTTPRedirectHandler, HTTPSHandler, ProxyHandler,
                            Request, build_opener)

from .protocol import ProtocolError, login_parameters, parse_reply
from .settings import Settings


class NetworkError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes


class Transport:
    def __init__(self, timeout: int):
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect(),
                                   HTTPSHandler(context=ssl.create_default_context()))

    def get(self, url: str, params: dict | None = None, limit: int = 262144) -> Response:
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        request = Request(url, headers={"User-Agent": "SrunGuard/1.0",
                                        "Cache-Control": "no-cache"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise NetworkError("Response too large.")
                return Response(response.status, body)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise NetworkError(f"HTTP {code}. Redirects blocked.") from None
        except (URLError, OSError, socket.timeout, HTTPException, ValueError):
            # Never stringify exceptions: their URLs may contain credential material.
            raise NetworkError("Request failed. Check connection, server or TLS.") from None


def valid_ip(value) -> str:
    try:
        address = ipaddress.ip_address(str(value))
        return "" if address.is_unspecified else str(address)
    except ValueError:
        return ""


def page_ip(page: str) -> str:
    patterns = (
        r'''["']?(?:online_ip|client_ip|user_ip|ip)["']?\s*[:=]\s*["']([^"']+)["']''',
        r'''(?:id|name)=["']user_ip["'][^>]*value=["']([^"']+)["']''',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, page, re.I):
            if address := valid_ip(match[1]):
                return address
    return ""


class SrunClient:
    # Exact response matching avoids treating a captive-portal HTML page as online.
    PROBES = (
        ("https://connectivitycheck.platform.hicloud.com/generate_204", 204, b""),
        ("http://www.msftconnecttest.com/connecttest", 200, b"Microsoft Connect Test"),
        ("https://cp.cloudflare.com/generate_204", 204, b""),
    )

    def __init__(self, settings: Settings, password: str, stop: Event,
                 transport: Transport | None = None):
        settings.validate()
        if not password:
            raise ValueError("Password required.")
        self.settings = settings
        self.password = password
        self.stop = stop
        self.http = transport or Transport(settings.timeout)
        # Assigned by the worker; messages must never contain URLs, IPs or credentials.
        self.diagnostic = lambda key, message: None

    def check_cancelled(self):
        if self.stop.is_set():
            raise Cancelled()

    def online(self) -> bool:
        for index, (url, status, expected) in enumerate(self.PROBES, 1):
            self.check_cancelled()
            started = time.monotonic()
            try:
                reply = self.http.get(url, limit=4096)
                matched = reply.status == status and reply.body == expected
                self.diagnostic(f"probe.{index}.{'ok' if matched else 'mismatch'}",
                    f"Probe {index}: status={reply.status}, expected_response={matched}, "
                    f"elapsed={time.monotonic() - started:.2f}s.")
                if matched:
                    return True
            except NetworkError as exc:
                self.diagnostic(f"probe.{index}.failed",
                    f"Probe {index}: {exc} Elapsed={time.monotonic() - started:.2f}s.")
        return False

    def _api(self, endpoint: str, params: dict) -> dict:
        self.check_cancelled()
        callback = "srun_" + secrets.token_hex(8)
        params = {**params, "callback": callback, "_": str(time.time_ns() // 1_000_000)}
        response = self.http.get(self.settings.portal.rstrip("/") + endpoint, params)
        return parse_reply(response.body.decode("utf-8", errors="replace"), callback)

    def login(self) -> str:
        self.check_cancelled()
        self.diagnostic("login.page", "Login step 1/3: requesting portal page.")
        # Page hints support older gateways; newer gateways return client_ip in challenge.
        page = self.http.get(self.settings.portal.rstrip("/") + "/srun_portal_pc",
                             {"ac_id": self.settings.ac_id, "theme": "pro"})
        address = page_ip(page.body.decode("utf-8", errors="replace"))
        self.diagnostic("login.challenge", "Login step 2/3: requesting challenge.")
        challenge = self._api("/cgi-bin/get_challenge",
                              {"username": self.settings.username, "ip": address})
        token = challenge.get("challenge")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-fA-F]{32,128}", token):
            raise ProtocolError("Invalid challenge. Check connection and AC ID.")
        address = valid_ip(challenge.get("client_ip")) or address
        if not address:
            raise ProtocolError("Client IP missing.")
        params = login_parameters(self.settings.username, self.password, address,
                                  self.settings.ac_id, token, self.settings.password_hmac)
        self.diagnostic("login.submit", "Login step 3/3: submitting authentication.")
        result = self._api("/cgi-bin/srun_portal", params)
        error = result.get("error")
        if error == "ok" or result.get("suc_msg") in ("login_ok", "ip_already_online_error"):
            self.diagnostic("login.accepted", "Authentication accepted; verifying Internet access.")
            return address
        # Whitelist messages, never echo arbitrary server text/passwords into GUI/logs.
        messages = {
            "password_error": "Invalid password.",
            "username_error": "Invalid username.",
            "user_not_exist": "Account not found.",
            "ip_already_online_error": "IP already online. Unexpected reply.",
            "sign_error": "Signature mismatch. Check HMAC mode and AC ID.",
        }
        code = str(result.get("error_msg") or error or "")
        raise ProtocolError(messages.get(code, "Authentication rejected. Check credentials and AC ID."))
