#!/opt/jumpserver/applet-env/bin/python3
"""
Generic RemoteApp launcher for Linux AppletHosts (xrdp).

Runs as the session's shell (invoked by .xsession). Replaces the role Tinker plays
on Windows: given a token_id staged for this OS account before the RDP login, it
resolves the full connection details from core and execs the target applet's
main.py with the same base64 payload every applet already expects.

Not installed via pip: it's a single file pushed by the deploy_applet_host_linux
Ansible playbook to every AppletHost, alongside a dedicated venv (see
playbook_linux.yml) that has the applets' own runtime deps (DrissionPage, pyotp, ...).
"""
import base64
import getpass
import json
import os
import subprocess
import sys
import tempfile
import traceback
import urllib.request
import urllib.error
import wsgiref.handlers

# Same lib the server already verifies against (apps/common/auth/signature.py),
# and the same scheme the Go connectors sign with (js-sdk-go/httplib/http_auth.go):
# HTTP Signature, hmac-sha256, over exactly ["(request-target)", "date"].
from httpsig import HeaderSigner

STAGING_DIR = "/opt/jumpserver/staging"
APPLETS_DIR = "/opt/jumpserver/applets"
SERVICE_ACCOUNT_FILE = "/opt/jumpserver/.service_account"
CORE_HOST_FILE = "/opt/jumpserver/.core_host"


def read_first_line(path):
    with open(path) as f:
        return f.readline().strip()


def fatal(msg):
    sys.stderr.write(msg + "\n")
    # Give the operator something to look at instead of an instantly-closing session.
    # msg often embeds a raw HTTP error body (quotes, apostrophes, newlines - e.g. a
    # DRF "doesn't have permission" message) - interpolating it straight into a shell
    # string (the previous `f"echo '{msg}'; ..."`) breaks the quoting and makes xterm's
    # `-e` command a syntax error, so it exits instantly with NOTHING shown - exactly
    # the "black screen, no popup" symptom this was supposed to prevent. Writing it to
    # a file and having the shell just `cat` that file sidesteps quoting entirely.
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(msg)
        msg_path = f.name
    subprocess.run(["xterm", "-e", "sh", "-c", f"cat {msg_path}; read -p 'press enter to close'"])
    sys.exit(1)


def read_tail(path, n=40):
    with open(path, "rb") as f:
        lines = f.read().splitlines()[-n:]
    return b"\n".join(lines).decode(errors="replace")


def load_pending_token():
    username = getpass.getuser()
    staging_path = os.path.join(STAGING_DIR, f"{username}.token")
    if not os.path.isfile(staging_path):
        fatal(f"No pending RemoteApp token staged for account '{username}' ({staging_path} not found)")
    token_id = read_first_line(staging_path)
    # One-shot: never reuse a staged token across sessions.
    os.remove(staging_path)
    return token_id


def fetch_secret_detail(token_id):
    core_host = read_first_line(CORE_HOST_FILE).rstrip("/")
    access_key, access_secret = read_first_line(SERVICE_ACCOUNT_FILE).split(":", 1)

    path = "/api/v1/authentication/super-connection-token/secret/"
    body = json.dumps({"id": token_id, "expire_now": True}).encode()

    signer = HeaderSigner(
        key_id=access_key, secret=access_secret,
        algorithm="hmac-sha256", headers=["(request-target)", "date"],
    )
    # HeaderSigner normalizes header names to lowercase in its return value; build
    # the base dict lowercase too, so `date` isn't duplicated as `Date` afterwards.
    headers = signer.sign({"date": wsgiref.handlers.format_date_time(None)},
                          method="POST", path=path)
    headers["content-type"] = "application/json"

    req = urllib.request.Request(core_host + path, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        fatal(f"Failed to resolve RemoteApp token {token_id}: HTTP {e.code} {e.read()[:500]}")


def main():
    token_id = load_pending_token()
    detail = fetch_secret_detail(token_id)

    app_name = (detail.get("connect_method") or {}).get("value")
    if not app_name:
        fatal(f"Token {token_id} has no applet connect_method, got: {detail.get('connect_method')}")

    applet_dir = os.path.join(APPLETS_DIR, app_name)
    main_py = os.path.join(applet_dir, "main.py")
    if not os.path.isfile(main_py):
        fatal(f"Applet '{app_name}' is not installed on this host ({main_py} not found)")

    payload = {
        "user": detail["user"],
        "asset": detail["asset"],
        "account": detail["account"],
        "platform": detail["platform"],
        "connect_options": detail.get("connect_options") or {},
    }
    payload_b64 = base64.b64encode(json.dumps(payload).encode()).decode()

    os.chdir(applet_dir)
    # Previously os.execv'd straight into the applet, matching Tinker's behavior
    # (session ends when the app exits) - but that means a crashing applet just
    # closes the RDP session with zero visible feedback (the black-screen reports
    # this kept producing) and nothing captured server-side either. Running it as
    # a child instead keeps the same end-of-session behavior (this script still
    # exits right after, ending the xrdp session) while letting a non-zero exit
    # surface through the same fatal()/xterm path load_pending_token() already uses.
    log_path = f"/tmp/jumpserver-applet-{getpass.getuser()}.log"
    with open(log_path, "wb") as log_file:
        result = subprocess.run(
            [sys.executable, main_py, payload_b64],
            stdout=log_file, stderr=subprocess.STDOUT,
        )
    if result.returncode != 0:
        fatal(
            f"Applet '{app_name}' exited with code {result.returncode}. "
            f"Last output (full log: {log_path}):\n{read_tail(log_path)}"
        )
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # Anything not already funneled through fatal() (a bare URLError from a
        # connection failure, a KeyError on an unexpected API response shape, etc.)
        # would otherwise just print to stderr - which nobody watching the RDP
        # session ever sees - and exit 1 silently. This is the catch-all that was
        # missing: every failure mode now surfaces the same way, via fatal()'s
        # xterm popup, instead of some producing a black screen with zero feedback.
        fatal("applet_shim.py crashed:\n" + traceback.format_exc())
