#!/opt/jumpserver/applet-env/bin/python3
"""
Heartbeat daemon for Linux AppletHosts.

Why this exists: AppletHost.load (apps/terminal/models/applet/host.py) delegates to
Terminal.load (apps/terminal/models/component/terminal.py), which reads a Redis cache
key TERMINAL_STATUS_<id> with a 3-minute TTL - written only by a successful
POST /api/v1/terminal/status/ (apps/terminal/api/component/status.py). With no cached
stat, ComputeLoadUtil.compute_load (apps/terminal/utils/components.py) falls straight to
"offline". On Windows, Tinker runs as a persistent service and (like the Go koko/lion/
magnus components) keeps that cache warm on its own. The Linux applet_shim.py only runs
transiently per RDP session and never calls that endpoint, so nothing kept it warm -
this script is the missing persistent poller, installed as its own systemd service
(see playbook_linux.yml) independent of any xrdp session.
"""
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import wsgiref.handlers

# Same HTTP Signature scheme applet_shim.py already uses against
# super-connection-token/secret/ - see that file for the verification-side reference.
from httpsig import HeaderSigner

SERVICE_ACCOUNT_FILE = "/opt/jumpserver/.service_account"
CORE_HOST_FILE = "/opt/jumpserver/.core_host"
HOST_ID_FILE = "/opt/jumpserver/.host_id"
STATUS_PATH = "/api/v1/terminal/status/"
INTERVAL_SECONDS = 90  # comfortably under the 3-minute TERMINAL_STATUS_<id> cache TTL


def read_first_line(path):
    with open(path) as f:
        return f.readline().strip()


def collect_stats():
    try:
        cpu_load = os.getloadavg()[0]
    except OSError:
        cpu_load = 0

    memory_used = 0
    try:
        meminfo = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                meminfo[key] = int(rest.strip().split()[0])  # kB
        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", total)
        if total:
            memory_used = round((total - available) / total * 100, 1)
    except (OSError, ValueError, KeyError):
        pass

    disk_used = 0
    try:
        usage = shutil.disk_usage("/")
        if usage.total:
            disk_used = round(usage.used / usage.total * 100, 1)
    except OSError:
        pass

    # No sessions to report here (applet_shim.py doesn't register session IDs
    # anywhere this daemon could read them from) - the status endpoint accepts an
    # empty list fine, only load/offline is what this daemon is here to fix.
    return {"sessions": [], "cpu_load": cpu_load, "memory_used": memory_used, "disk_used": disk_used}


def _signed_request(core_host, path, access_key, access_secret, body=b"{}"):
    signer = HeaderSigner(
        key_id=access_key, secret=access_secret,
        algorithm="hmac-sha256", headers=["(request-target)", "date"],
    )
    headers = signer.sign({"date": wsgiref.handlers.format_date_time(None)},
                          method="POST", path=path)
    headers["content-type"] = "application/json"

    req = urllib.request.Request(core_host + path, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def send_heartbeat(core_host, access_key, access_secret):
    body = json.dumps(collect_stats()).encode()
    _signed_request(core_host, STATUS_PATH, access_key, access_secret, body)


def call_startup(core_host, host_id, access_key, access_secret):
    # Registering a service account only creates a brand-new Terminal row - it
    # never links back to this AppletHost, so AppletHost.load stays "offline"
    # forever (see AppletHost.load / AppletHost.check_terminal_binding) no matter
    # how well the heartbeat above is doing. That link only happens when something
    # calls this endpoint as the service account. Tinker does this on its own
    # service start on Windows; this is the Linux equivalent. Safe to call every
    # cycle - check_terminal_binding() is a no-op once already bound.
    _signed_request(core_host, f"/api/v1/terminal/applet-hosts/{host_id}/startup/",
                     access_key, access_secret)


def main():
    core_host = read_first_line(CORE_HOST_FILE).rstrip("/")
    access_key, access_secret = read_first_line(SERVICE_ACCOUNT_FILE).split(":", 1)
    host_id = read_first_line(HOST_ID_FILE)

    while True:
        try:
            send_heartbeat(core_host, access_key, access_secret)
        except urllib.error.HTTPError as e:
            # e.__str__() only gives the status line ("HTTP Error 401: Unauthorized"),
            # which throws away the actual reason - common/auth/signature.py raises two
            # distinct messages for a 401 ("Invalid signature." for a bad key_id/signature
            # vs "Ip is not in access ip list." for an IP outside the access key's allow
            # list), and only the DRF error body carries that distinction. See
            # applet_shim.py's fetch_secret_detail() for the same read-the-body pattern.
            detail = e.read()[:500]
            sys.stderr.write(f"heartbeat failed: HTTP {e.code} {detail}\n")
        except (urllib.error.URLError, OSError) as e:
            sys.stderr.write(f"heartbeat failed: {e}\n")

        try:
            call_startup(core_host, host_id, access_key, access_secret)
        except urllib.error.HTTPError as e:
            detail = e.read()[:500]
            sys.stderr.write(f"startup binding failed: HTTP {e.code} {detail}\n")
        except (urllib.error.URLError, OSError) as e:
            sys.stderr.write(f"startup binding failed: {e}\n")

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
