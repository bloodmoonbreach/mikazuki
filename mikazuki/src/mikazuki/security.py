"""Login (session cookie) and CSRF protection.

The account is created in the browser on first run: until then every page leads to /setup/account,
which asks for a one-time setup code printed in the server log (and saved in the data folder), so
nobody else on the network can claim a fresh install first. The password is stored as a scrypt hash
in `<data>/.account`. MIKAZUKI_PASSWORD (and MIKAZUKI_USER, default "admin") still work and take
precedence, for setups that keep secrets in the environment.

Sessions are signed cookies: HttpOnly, SameSite=Lax, signed with a key derived from a server secret
*and* the password hash, so changing the password signs everyone out. Five wrong passwords from one
address lock it out for a few minutes. Every POST must also carry the CSRF token from a cookie.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

CSRF_COOKIE = "mikazuki_csrf"
SESSION_COOKIE = "mikazuki_session"
SESSION_SHORT = 12 * 3600  # without "keep me signed in"
SESSION_LONG = 30 * 24 * 3600
MAX_FAILS, LOCK_WINDOW = 5, 10 * 60
PUBLIC = ("/login", "/static/", "/healthz", "/setup/account")
ACCOUNT_FILE, SETUP_CODE_FILE = ".account", "setup-code.txt"
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
MIN_PASSWORD = 10


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), dklen=32, **_SCRYPT)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def password_problem(password: str, user: str = "") -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if user and password.lower() == user.lower():
        return "The password can't be the username."
    if len(set(password)) < 4:
        return "That password is too easy to guess."
    return None


def _write_private(path: Path, data: bytes | str) -> None:
    old = os.umask(0o077)
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data.encode() if isinstance(data, str) else data)
        tmp.replace(path)
    finally:
        os.umask(old)


class Auth:
    """Holds the credentials and signing key; one instance per app. Unconfigured until an account exists."""

    def __init__(self, data_dir: Path, secret: bytes):
        self.dir, self.secret = Path(data_dir), secret
        self._fails: dict[str, list[float]] = {}
        self.user, self._check, self.key = "", None, b""
        self.reload()

    def reload(self) -> None:
        env_pw = os.environ.get("MIKAZUKI_PASSWORD")
        if env_pw:
            self.user, stamp = os.environ.get("MIKAZUKI_USER", "admin"), env_pw
            self._check = lambda pw: hmac.compare_digest(pw.encode(), env_pw.encode())
            self.from_env = True
        elif (f := self.dir / ACCOUNT_FILE).exists():
            import json
            acc = json.loads(f.read_text(encoding="utf-8"))
            self.user, stamp = acc["user"], acc["hash"]
            self._check = lambda pw: verify_password(pw, acc["hash"])
            self.from_env = False
        else:
            self.user, stamp, self._check, self.from_env = "", "", None, False
        self.key = hmac.new(self.secret, b"session|" + stamp.encode(), hashlib.sha256).digest()

    @property
    def configured(self) -> bool:
        return self._check is not None

    # -- first run
    def setup_code(self) -> str:
        """The one-time code that unlocks account creation; created on first use, kept in the data folder."""
        f = self.dir / SETUP_CODE_FILE
        if f.exists():
            return f.read_text(encoding="utf-8").strip()
        code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        _write_private(f, code + "\n")
        return code

    def create_account(self, user: str, password: str) -> None:
        import json
        if self.configured:
            raise ValueError("an account already exists")
        _write_private(self.dir / ACCOUNT_FILE, json.dumps({"user": user, "hash": hash_password(password)}))
        (self.dir / SETUP_CODE_FILE).unlink(missing_ok=True)
        self.reload()

    def change_password(self, password: str) -> None:
        import json
        if self.from_env:
            raise ValueError("the password is set by MIKAZUKI_PASSWORD; change it there")
        _write_private(self.dir / ACCOUNT_FILE, json.dumps({"user": self.user, "hash": hash_password(password)}))
        self.reload()

    # -- sessions
    def make(self, long: bool) -> tuple[str, int]:
        ttl = SESSION_LONG if long else SESSION_SHORT
        payload = f"{self.user}|{int(time.time()) + ttl}".encode()
        sig = hmac.new(self.key, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload).decode() + "." + base64.urlsafe_b64encode(sig).decode(), ttl

    def valid(self, cookie: str | None) -> bool:
        if not cookie or "." not in cookie:
            return False
        try:
            p, s = cookie.split(".", 1)
            payload, sig = base64.urlsafe_b64decode(p), base64.urlsafe_b64decode(s)
        except (ValueError, TypeError):
            return False
        if not hmac.compare_digest(sig, hmac.new(self.key, payload, hashlib.sha256).digest()):
            return False
        user, _, exp = payload.decode(errors="replace").partition("|")
        return self.configured and user == self.user and exp.isdigit() and int(exp) > time.time()

    # -- password checks with a lockout
    def locked_for(self, ip: str) -> int:
        now = time.time()
        fails = [t for t in self._fails.get(ip, []) if now - t < LOCK_WINDOW]
        self._fails[ip] = fails
        return int(fails[0] + LOCK_WINDOW - now) if len(fails) >= MAX_FAILS else 0

    def check(self, ip: str, user: str, password: str) -> bool:
        if not self.configured:
            return False
        ok = hmac.compare_digest(user.encode(), self.user.encode()) & bool(self._check(password))
        if ok:
            self._fails.pop(ip, None)
        else:
            self._fails.setdefault(ip, []).append(time.time())
        return bool(ok)


def safe_next(target: str | None) -> str:
    """Only same-site paths: '/tracker' yes; '//evil.com', 'https://…', '/login' no."""
    t = (target or "").strip()
    if not re.fullmatch(r"/(?![/\\])[^\s]*", t) or t.startswith("/login"):
        return "/"
    return t


def load_secret(data_dir: Path) -> bytes:
    env = os.environ.get("MIKAZUKI_SECRET")
    if env:
        return env.encode()
    f = Path(data_dir) / ".secret"
    if f.exists():
        return f.read_bytes()
    value = secrets.token_bytes(32)
    old = os.umask(0o077)
    try:
        f.write_bytes(value)
    finally:
        os.umask(old)
    return value


class RequireLogin(BaseHTTPMiddleware):
    def __init__(self, app, auth: Auth):
        super().__init__(app)
        self.auth = auth

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path.startswith(PUBLIC) or self.auth.valid(request.cookies.get(SESSION_COOKIE)):
            return await call_next(request)
        if not self.auth.configured:
            if request.method == "GET":
                return RedirectResponse("/setup/account", status_code=303)
            return PlainTextResponse("No account yet: open /setup/account first.", 401)
        if request.method == "GET":
            from urllib.parse import quote
            target = path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse(f"/login?next={quote(target, safe='/')}", status_code=303)
        return PlainTextResponse("Signed out: reload the page and sign in again.", 401)


class CsrfError(Exception):
    pass


async def check_csrf(request: Request) -> None:
    """FastAPI dependency: every POST must echo the token from our cookie. Runs inside the endpoint, so
    the parsed form is cached on the request and the route's Form() parameters still see it."""
    if request.method != "POST":
        return
    form = await request.form()
    sent = str(form.get("csrf", ""))
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not cookie or not sent or not hmac.compare_digest(sent, cookie):
        raise CsrfError()


async def csrf_error(request: Request, exc: CsrfError) -> Response:
    return PlainTextResponse("Form expired or sent from another site; reload the page and try again.", 403)


class Csrf(BaseHTTPMiddleware):
    """Hands out the CSRF cookie and sets security headers. (The check itself is `check_csrf`.)"""

    async def dispatch(self, request: Request, call_next) -> Response:
        token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
        request.state.csrf = token
        response = await call_next(request)
        if not request.cookies.get(CSRF_COOKIE):
            response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="strict")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-src 'self'; object-src 'self'")
        return response


def install(app, data_dir: Path) -> Auth:
    from fastapi import Depends

    app.router.dependencies.append(Depends(check_csrf))
    app.add_exception_handler(CsrfError, csrf_error)
    app.add_middleware(Csrf)  # inner: also runs for /login, so the login form gets a CSRF token
    auth = Auth(data_dir, load_secret(data_dir))
    if not auth.configured:
        print(f"mikazuki: first run. Open the app in your browser to create your account; setup code: "
              f"{auth.setup_code()}  (also in {Path(data_dir) / SETUP_CODE_FILE})", flush=True)
    app.add_middleware(RequireLogin, auth=auth)  # outer
    return auth
