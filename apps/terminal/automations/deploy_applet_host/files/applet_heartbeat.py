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


def send_heartbeat(core_host, access_key, access_secret):
    body = json.dumps(collect_stats()).encode()
    signer = HeaderSigner(
        key_id=access_key, secret=access_secret,
        algorithm="hmac-sha256", headers=["(request-target)", "date"],
    )
    headers = signer.sign({"date": wsgiref.handlers.format_date_time(None)},
                          method="POST", path=STATUS_PATH)
    headers["content-type"] = "application/json"

    req = urllib.request.Request(core_host + STATUS_PATH, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def main():
    core_host = read_first_line(CORE_HOST_FILE).rstrip("/")
    access_key, access_secret = read_first_line(SERVICE_ACCOUNT_FILE).split(":", 1)

    while True:
        try:
            send_heartbeat(core_host, access_key, access_secret)
        except (urllib.error.URLError, OSError) as e:
            sys.stderr.write(f"heartbeat failed: {e}\n")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
