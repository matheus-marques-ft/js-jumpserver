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
import threading
import time
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


def maximize_active_window_async():
    """Kiosk mode runs with no window manager at all (see startwm.sh's
    "Make every xrdp session run the shim instead of a desktop" task in
    playbook_linux.yml) - a freshly launched app just opens at whatever size it
    picks by default instead of filling the display. There's nothing WM-specific
    to hook into, so poll for the app's window with xdotool and force it to
    cover the whole screen once it appears. Runs in a background thread since
    the app itself is launched as a blocking subprocess.run() call.

    Deliberately `search`, not `getactivewindow`: "active window" is an EWMH
    concept (_NET_ACTIVE_WINDOW) that only exists because a window manager
    maintains it - with none running here, getactivewindow never finds
    anything and this silently never resized anything. `search` walks the X11
    window tree directly (XQueryTree), so it works with no WM at all. The
    session's own display only ever has this one app on it, so a bare ".*"
    (any window with a non-empty title) is enough to find it.

    A single resize at startup isn't enough: the client can change the virtual
    screen's resolution mid-session (js-lion's ResizeObserver + guacd's
    resize-method=display-update fire whenever the browser's own display
    element changes size, not just once), and xorgxrdp applies that as a real
    RandR resolution change on this session's X screen. With no window
    manager to notice the screen changed and resize the one window that's on
    it, the app keeps whatever size it had when it was first fit, no longer
    matching the display - same symptom as never having fit it at all. So
    this keeps polling for the rest of the session (thread is daemon=True,
    dies on its own when the process exits) and re-applies the fullscreen fit
    whenever the reported geometry differs from what was last applied."""

    debug_log_path = f"/tmp/jumpserver-resize-debug-{getpass.getuser()}.log"

    def _debug(msg):
        try:
            with open(debug_log_path, "a") as f:
                f.write(f"{time.time():.3f} {msg}\n")
        except Exception:
            pass

    def _screen_geometry():
        result = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    _x_display_holder = {}

    def _get_x_display():
        if "d" not in _x_display_holder:
            # Local import, deliberately not at module level: python-xlib is
            # only needed for this one filter (telling a menu/popup apart from
            # a real window) - a host that hasn't been re-provisioned with the
            # playbook_linux.yml pip requirement yet shouldn't have THIS
            # import blow up the whole shim (which also handles the actual
            # applet launch, entirely unrelated to resize) at module load
            # time. ImportError here is just one more failure mode the
            # caller's try/except already treats as "can't tell, don't
            # exclude" - same fallback used for a raw connection failure.
            from Xlib import display
            _x_display_holder["d"] = display.Display()
        return _x_display_holder["d"]

    def _is_top_level_window(window_id):
        # Chrome's own menus/dropdowns (e.g. the 3-dot options menu) are
        # separate X11 windows too, and _found_window_ids() below picks them
        # up like any other - confirmed live: forcing every found window to
        # 100%x100% stretched the menu popup itself across the screen while
        # leaving the actual browser window untouched. override-redirect is
        # the actual X11 mechanism a popup uses to place itself without going
        # through window management, so it's the correct signal to tell a
        # menu apart from a real top-level window (the outer frame and the
        # separate content surface are both override-redirect=0).
        #
        # Queried via python-xlib (raw X11 protocol) rather than shelling out
        # to xwininfo: confirmed live that xwininfo isn't even installed on
        # at least one tested AL2023 AppletHost (xorg-x11-utils doesn't
        # resolve there), so the filter silently never excluded anything.
        # A pip package inside the venv this playbook already fully controls
        # sidesteps that distro-repo guessing entirely. If the query itself
        # fails for any reason, don't exclude the window - this whole thread
        # has no outer try/except, so letting an exception escape here would
        # silently kill it and stop ALL future resizing for the rest of the
        # session, not just skip this one filter check.
        try:
            window = _get_x_display().create_resource_object("window", int(window_id))
            is_override_redirect = bool(window.get_attributes().override_redirect)
        except Exception as exc:
            _debug(f"_is_top_level_window window={window_id} Xlib query FAILED: {exc!r}")
            return True
        _debug(f"_is_top_level_window window={window_id} override_redirect={is_override_redirect}")
        return not is_override_redirect

    def _found_window_ids():
        result = subprocess.run(
            ["xdotool", "search", "--onlyvisible", ".*"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return []
        ids = [line for line in result.stdout.splitlines() if line.strip()]
        return [window_id for window_id in ids if _is_top_level_window(window_id)]

    def _fit_window():
        # Deliberately every visible TOP-LEVEL window, not just the first
        # (--limit 1 used to stop here) - the debug log showed a second
        # window (a GPU/compositor content surface, separate from the outer
        # browser frame) appearing partway into the session, and only the
        # first (the frame) was ever getting resized. window.innerWidth
        # staying frozen while the outer frame's own X11 geometry genuinely
        # changed (confirmed via a live CDP query) pointed straight at this -
        # the content surface never got its own resize. _found_window_ids()
        # already excludes override-redirect windows (menus/dropdowns), so
        # nothing here needs to special-case those.
        window_ids = _found_window_ids()
        ok = True
        for window_id in window_ids:
            result = subprocess.run(
                ["xdotool", "windowsize", window_id, "100%", "100%",
                 "windowmove", window_id, "0", "0"],
                capture_output=True, text=True,
            )
            ok = ok and result.returncode == 0
            if result.returncode != 0:
                _debug(f"_fit_window window={window_id} FAILED rc={result.returncode} stderr={result.stderr.strip()!r}")
        after = subprocess.run(
            ["xdotool", "search", "--onlyvisible", ".*", "getwindowgeometry"],
            capture_output=True, text=True,
        ).stdout.strip().replace("\n", " | ")
        _debug(f"_fit_window ok={ok} windows_seen={window_ids} after_geometry={after!r}")
        return ok and bool(window_ids)

    def _run():
        _debug(f"maximize_active_window_async started, log={debug_log_path}")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if _fit_window():
                break
            time.sleep(0.5)
        else:
            _debug("gave up waiting for a window within 20s")
            return

        last_geometry = _screen_geometry()
        last_window_ids = set(_found_window_ids())
        _debug(f"initial fit done, last_geometry={last_geometry!r}, last_window_ids={last_window_ids}")
        while True:
            time.sleep(1)
            geometry = _screen_geometry()
            window_ids = set(_found_window_ids())
            geometry_changed = geometry and geometry != last_geometry
            # Chrome creates its real content surface (a second window,
            # separate from the outer frame) lazily, sometime after launch -
            # confirmed live: the very first fit only ever sees the frame
            # (the content window doesn't exist yet), so it never gets sized
            # until something ELSE happens to trigger a refit later. Reacting
            # to the window SET changing (not just the screen size) means we
            # catch that new window and size it immediately, instead of
            # leaving it stuck at whatever default Chrome gave it until the
            # next real screen resize happens to sweep it up as a side effect.
            windows_changed = window_ids != last_window_ids
            if geometry_changed or windows_changed:
                _debug(
                    f"refitting (geometry_changed={geometry_changed} {last_geometry!r}->{geometry!r}, "
                    f"windows_changed={windows_changed} {last_window_ids}->{window_ids})"
                )
                last_geometry = geometry
                last_window_ids = window_ids
                _fit_window()

    threading.Thread(target=_run, daemon=True).start()


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
        # Part of the official main.py contract (confirmed against JumpServer's
        # own applet demo project: the base64 payload includes app_name/protocol/
        # user/asset/account/platform) - chrome doesn't need it (it never calls
        # asset.get_protocol_port()), so its absence went unnoticed until an
        # applet that does need it (mongodb_frete_v1, resolving the Mongo port
        # via asset.get_protocol_port(self.protocol)) was added.
        "protocol": detail.get("protocol"),
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
    maximize_active_window_async()
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
