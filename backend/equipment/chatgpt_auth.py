"""Local-only SIWC login utility. Credentials are never returned by PPE APIs."""

import argparse
import base64
from contextlib import contextmanager
import ctypes
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4
import webbrowser

import httpx
import jwt

from backend.equipment.provider import ProviderError
from backend.media.paths import WORK_ROOT

ISSUER = "https://auth.openai.com"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
ROOT = WORK_ROOT / "chatgpt_auth"
LOCAL_LOCK = threading.Lock()


def _protect(data: bytes, decrypt=False):
    if os.name != "nt":
        return data
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("credential_protection_failed")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


@contextmanager
def credential_lock():
    # Serialize refresh rotation across API threads and separate server processes.
    with LOCAL_LOCK:
        ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = ROOT / "session.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ProviderError("provider_unavailable") from None
        try:
            os.close(fd)
            yield
        finally:
            lock.unlink(missing_ok=True)


def load_credentials():
    try:
        return json.loads(_protect((ROOT / "credentials.dat").read_bytes(), decrypt=True))
    except (OSError, ValueError):
        raise ProviderError("provider_auth_required") from None


def save_credentials(record):
    temporary = ROOT / (uuid4().hex + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(_protect(json.dumps(record).encode("utf-8")))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, ROOT / "credentials.dat")
    finally:
        temporary.unlink(missing_ok=True)


def token_request(body, timeout=10):
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.post(TOKEN_URL, data=body)
    if response.status_code != 200:
        raise ProviderError("provider_auth_required" if response.status_code in {400, 401, 403}
                            else "provider_unavailable")
    result = response.json()
    if result.get("token_type", "").lower() != "bearer" or not result.get("access_token"):
        raise ProviderError("provider_auth_required")
    return result


def validate_identity(id_token, client_id, nonce=None):
    with httpx.Client(timeout=10, trust_env=False) as client:
        response = client.get(ISSUER + "/.well-known/jwks.json")
        response.raise_for_status()
    header = jwt.get_unverified_header(id_token)
    algorithm = header.get("alg")
    if algorithm not in {"RS256", "ES256"}:
        raise ProviderError("provider_auth_required")
    keys = [key for key in response.json()["keys"] if key.get("kid") == header.get("kid")]
    if len(keys) != 1:
        raise ProviderError("provider_auth_required")
    key = jwt.PyJWK.from_dict(keys[0], algorithm=algorithm)
    identity = jwt.decode(id_token, key.key, algorithms=[algorithm], issuer=ISSUER,
                          audience=client_id, leeway=5,
                          options={"require": ["sub", "exp", "iat", "iss", "aud"]})
    if nonce is not None and not secrets.compare_digest(identity.get("nonce", ""), nonce):
        raise ProviderError("provider_auth_required")
    return identity


def access_token(timeout=10):
    with credential_lock():
        record = load_credentials()
        if "chatgpt.tokens.use.direct" not in record.get("scopes", []):
            raise ProviderError("provider_auth_required")
        if record["expires_at"] <= time.time() + 60:
            if not record.get("refresh_token"):
                raise ProviderError("provider_auth_required")
            result = token_request({"grant_type": "refresh_token", "client_id": record["client_id"],
                                    "refresh_token": record["refresh_token"], "resource": RESOURCE}, timeout)
            scopes = result.get("scope", " ".join(record["scopes"])).split()
            if "chatgpt.tokens.use.direct" not in scopes:
                raise ProviderError("provider_auth_required")
            if result.get("id_token"):
                identity = validate_identity(result["id_token"], record["client_id"])
                if identity["sub"] != record["subject"]:
                    raise ProviderError("provider_auth_required")
                record["id_token"] = result["id_token"]
            record.update(access_token=result["access_token"],
                          refresh_token=result.get("refresh_token", record["refresh_token"]),
                          expires_at=time.time() + float(result["expires_in"]), scopes=scopes)
            save_credentials(record)
        return record["access_token"]


def login():
    # CLI only: bind loopback before opening the browser; never expose a callback on LAN.
    with credential_lock():
        host_path = ROOT / "host_id"
        if not host_path.exists():
            host_path.write_text("urn:uuid:" + str(uuid4()), encoding="ascii")
        host = host_path.read_text(encoding="ascii").strip()
        try:
            previous = load_credentials()
        except ProviderError:
            previous = None
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        callback = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Callback codes and query strings must never be logged.

            def do_GET(self):
                parsed = urlsplit(self.path)
                args = parse_qs(parsed.query)
                valid = parsed.path == "/auth/callback" and secrets.compare_digest(args.get("state", [""])[0], state)
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"Return to the terminal to check the login result." if valid else b"Invalid callback.")
                if valid:
                    callback.update({key: values[0] for key, values in args.items()})

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            server.timeout = 1
            redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
            params = {"client_id": previous["client_id"] if previous else "dynamic_agent_client",
                      "ext_agent_host_id": host, "response_type": "code", "redirect_uri": redirect,
                      "scope": SCOPES, "resource": RESOURCE, "state": state, "nonce": nonce,
                      "code_challenge_method": "S256",
                      "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()}
            if previous:
                params["id_token_hint"] = previous["id_token"]
            else:
                params["agent_name_hint"] = "CBNU CAPSTONE Kyo Equipment Recognition"
            webbrowser.open(ISSUER + "/api/accounts/authorize?" + urlencode(params))
            print("Complete ChatGPT sign-in and plan-usage consent in the browser (5 minutes).")
            deadline = time.monotonic() + 300
            while not callback and time.monotonic() < deadline:
                server.handle_request()
        if not callback or callback.get("error") or not callback.get("code"):
            raise ProviderError("provider_auth_required")
        client_id = callback.get("client_id") or (previous["client_id"] if previous else "")
        if not client_id or client_id == "dynamic_agent_client" or (previous and client_id != previous["client_id"]):
            raise ProviderError("provider_auth_required")
        tokens = token_request({"grant_type": "authorization_code", "client_id": client_id,
                                "code": callback["code"], "code_verifier": verifier,
                                "redirect_uri": redirect, "resource": RESOURCE})
        identity = validate_identity(tokens["id_token"], client_id, nonce)
        scopes = tokens.get("scope", "").split()
        if "chatgpt.tokens.use.direct" not in scopes or (previous and identity["sub"] != previous["subject"]):
            raise ProviderError("provider_auth_required")
        save_credentials({**tokens, "client_id": client_id, "subject": identity["sub"],
                          "ext_agent_host_id": host, "scopes": scopes,
                          "expires_at": time.time() + float(tokens["expires_in"])})
        print("ChatGPT plan connection saved. No image was sent.")


def logout():
    with credential_lock():
        record = load_credentials()
        confirmed = False
        try:
            with httpx.Client(timeout=10, trust_env=False) as client:
                discovery = client.get(ISSUER + "/.well-known/openid-configuration")
                discovery.raise_for_status()
                endpoint = discovery.json()["revocation_endpoint"]
                if urlsplit(endpoint).scheme != "https" or urlsplit(endpoint).netloc != "auth.openai.com":
                    raise ValueError("invalid_revocation_endpoint")
                result = client.post(endpoint, data={"token": record["refresh_token"],
                                                    "token_type_hint": "refresh_token", "client_id": record["client_id"]})
                confirmed = result.status_code == 200
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        (ROOT / "credentials.dat").unlink(missing_ok=True)
        print("Local credentials removed. " + ("Remote session revoked." if confirmed else
              "Remote revocation not confirmed; disconnect this app in ChatGPT Settings."))


def main():
    parser = argparse.ArgumentParser(description="Local ChatGPT plan connection; never prints tokens.")
    parser.add_argument("action", choices=["login", "status", "models", "logout"])
    action = parser.parse_args().action
    try:
        if action == "login":
            login()
        elif action == "logout":
            logout()
        elif action == "status":
            with credential_lock():
                record = load_credentials()
                print("Connected; token " + ("expired (refresh on next request)" if record["expires_at"] <= time.time() else "valid"))
        else:
            with httpx.Client(timeout=10, trust_env=False) as client:
                response = client.get(RESOURCE + "/models", headers={"Authorization": "Bearer " + access_token()})
                response.raise_for_status()
            for model in response.json().get("models", []):
                if model.get("visibility") == "list":
                    print(model["slug"], model.get("display_name", ""))
    except (ProviderError, httpx.HTTPError, jwt.PyJWTError, OSError, ValueError, KeyError, ImportError) as error:
        print(getattr(error, "code", "chatgpt_connection_failed"))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
