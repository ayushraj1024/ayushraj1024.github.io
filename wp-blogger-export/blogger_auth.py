#!/usr/bin/env python3
"""Google OAuth and Blogger API helpers (stdlib only)."""

from __future__ import annotations

import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

BLOGGER_SCOPE = "https://www.googleapis.com/auth/blogger"
TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
BLOGGER_API_BASE = "https://www.googleapis.com/blogger/v3"


@dataclass
class OAuthClient:
    client_id: str
    client_secret: str


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str | None
    expires_at: float
    token_type: str = "Bearer"

    @property
    def expired(self) -> bool:
        return time.time() >= (self.expires_at - 60)


def find_credentials_file(directory: Path | None = None) -> Path:
    root = directory or Path(__file__).resolve().parent
    candidates = sorted(root.glob("client_secret*.json"))
    if not candidates:
        fallback = root / "credentials.json"
        if fallback.exists():
            return fallback
        raise FileNotFoundError(
            "No OAuth credentials found. Place your Desktop client JSON in "
            f"{root} as credentials.json or client_secret_*.json"
        )
    return candidates[0]


def load_oauth_client(credentials_path: Path) -> OAuthClient:
    payload = json.loads(credentials_path.read_text(encoding="utf-8"))
    section = payload.get("installed") or payload.get("web")
    if not section:
        raise ValueError(f"Unsupported credentials format in {credentials_path}")
    return OAuthClient(
        client_id=section["client_id"],
        client_secret=section["client_secret"],
    )


def load_token(token_path: Path) -> TokenBundle:
    payload = json.loads(token_path.read_text(encoding="utf-8"))
    return TokenBundle(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_at=float(payload["expires_at"]),
        token_type=payload.get("token_type", "Bearer"),
    )


def save_token(token_path: Path, token: TokenBundle) -> None:
    payload = {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "expires_at": token.expires_at,
        "token_type": token.token_type,
    }
    token_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail}") from exc


def exchange_code(
    client: OAuthClient,
    code: str,
    redirect_uri: str,
) -> TokenBundle:
    payload = _post_form(
        TOKEN_URI,
        {
            "code": code,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    return TokenBundle(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_at=time.time() + float(payload.get("expires_in", 3600)),
        token_type=payload.get("token_type", "Bearer"),
    )


def refresh_access_token(client: OAuthClient, refresh_token: str) -> TokenBundle:
    payload = _post_form(
        TOKEN_URI,
        {
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    return TokenBundle(
        access_token=payload["access_token"],
        refresh_token=refresh_token,
        expires_at=time.time() + float(payload.get("expires_in", 3600)),
        token_type=payload.get("token_type", "Bearer"),
    )


class _OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.auth_error = query.get("error", [None])[0]  # type: ignore[attr-defined]
        self.server.auth_code = query.get("code", [None])[0]  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if self.server.auth_code:  # type: ignore[attr-defined]
            message = (
                "<h1>Authorization complete</h1>"
                "<p>You can close this tab and return to the terminal.</p>"
            )
        else:
            message = (
                "<h1>Authorization failed</h1>"
                "<p>Return to the terminal for details.</p>"
            )
        self.wfile.write(message.encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        return


def run_oauth_flow(
    credentials_path: Path,
    token_path: Path,
    *,
    port: int = 8080,
) -> TokenBundle:
    client = load_oauth_client(credentials_path)
    redirect_uri = f"http://localhost:{port}/"
    state = secrets.token_urlsafe(16)
    auth_query = urllib.parse.urlencode(
        {
            "client_id": client.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": BLOGGER_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    auth_url = f"{AUTH_URI}?{auth_query}"

    server = HTTPServer(("localhost", port), _OAuthHandler)
    server.auth_code = None  # type: ignore[attr-defined]
    server.auth_error = None  # type: ignore[attr-defined]
    server.timeout = 1

    print(f"Opening browser for Google authorization on {redirect_uri}", file=sys.stderr)
    webbrowser.open(auth_url)

    deadline = time.time() + 300
    while time.time() < deadline:
        server.handle_request()
        if server.auth_code or server.auth_error:  # type: ignore[attr-defined]
            break

    if server.auth_error:  # type: ignore[attr-defined]
        raise RuntimeError(f"OAuth error: {server.auth_error}")  # type: ignore[attr-defined]
    if not server.auth_code:  # type: ignore[attr-defined]
        raise RuntimeError("Timed out waiting for OAuth authorization.")

    token = exchange_code(client, server.auth_code, redirect_uri)  # type: ignore[attr-defined]
    if not token.refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token. Revoke app access in your "
            "Google Account permissions and run auth again."
        )

    save_token(token_path, token)
    return token


def get_access_token(credentials_path: Path, token_path: Path) -> str:
    client = load_oauth_client(credentials_path)
    token = load_token(token_path)
    if not token.expired:
        return token.access_token
    if not token.refresh_token:
        raise RuntimeError(
            f"Token in {token_path} is expired and has no refresh token. Run auth.py again."
        )
    refreshed = refresh_access_token(client, token.refresh_token)
    save_token(token_path, refreshed)
    return refreshed.access_token


def blogger_request(
    method: str,
    url: str,
    access_token: str,
    *,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> dict[str, Any]:
    final_url = url
    if query:
        separator = "&" if "?" in url else "?"
        final_url = f"{url}{separator}{urllib.parse.urlencode(query)}"

    data = None
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "wp-blogger-export/1.0",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(final_url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {final_url}: {detail}") from exc


def list_blogs(access_token: str) -> list[dict[str, Any]]:
    payload = blogger_request(
        "GET",
        f"{BLOGGER_API_BASE}/users/self/blogs",
        access_token,
    )
    return payload.get("items", [])
