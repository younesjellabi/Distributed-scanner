"""
Distributed network scanner — Phase 1 probe-only
stdlib only, no third-party dependencies.
Runs as UID 1000, CAP_NET_RAW, read-only rootfs.
"""

import json
import signal
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

INVENTORY_FILE = Path("/app/inventory.json")
OUTPUT_DIR     = Path("/data/scans")
SCANNER_SITE   = "site1"
TIMEOUT        = 3
SSH_PORT       = 22
HTTPS_PORT     = 443

# ── L4: Graceful shutdown ──────────────────────────────────────────────────────

shutdown_requested = False

def handle_shutdown(signum, frame):
    global shutdown_requested
    shutdown_requested = True
    log("warning", "shutdown signal received", signum=signum)

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT,  handle_shutdown)

# ── L3: Structured logging to stderr ──────────────────────────────────────────

def log(level, msg, **kwargs):
    event = {
        "ts":    datetime.now(timezone.utc).isoformat(),
        "level": level,
        "msg":   msg,
        **kwargs,
    }
    print(json.dumps(event), file=sys.stderr, flush=True)

# ── Probe functions ───────────────────────────────────────────────────────────

def probe_ssh(host, port=SSH_PORT, timeout=TIMEOUT):
    """
    Full TCP connect + banner read.
    Returns 'yes' only if the SSH banner starts with 'SSH-'.
    Firewalls using User-ID that complete TCP but block at the
    application layer will correctly return 'no'.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            banner = s.recv(256).decode("ascii", errors="ignore").strip()
            accessible = banner.startswith("SSH-")
            log("info", "ssh probe",
                host=host, accessible=accessible, banner=banner[:40])
            return "yes" if accessible else "no"
    except socket.timeout:
        log("info", "ssh probe timeout", host=host)
        return "no"
    except ConnectionRefusedError:
        log("info", "ssh probe refused", host=host)
        return "no"
    except OSError as e:
        log("info", "ssh probe error", host=host, error=str(e))
        return "no"


def probe_https(host, port=HTTPS_PORT, timeout=TIMEOUT):
    """
    Full TCP connect + TLS handshake + GET /.
    Returns 'yes' if any HTTP response is received.
    Certificate validation disabled — probing reachability only.
    SSL error after TCP connect = host reachable, returns 'yes'.
    """
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE

        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as s:
                s.sendall(
                    b"GET / HTTP/1.0\r\nHost: " +
                    host.encode() + b"\r\n\r\n"
                )
                response  = s.recv(256).decode("ascii", errors="ignore")
                accessible = response.startswith("HTTP/")
                status     = response.split("\r\n")[0][:40] if accessible else ""
                log("info", "https probe",
                    host=host, accessible=accessible, status=status)
                return "yes" if accessible else "no"
    except socket.timeout:
        log("info", "https probe timeout", host=host)
        return "no"
    except ssl.SSLError as e:
        log("info", "https probe ssl error (host reachable)",
            host=host, error=str(e))
        return "yes"
    except ConnectionRefusedError:
        log("info", "https probe refused", host=host)
        return "no"
    except OSError as e:
        log("info", "https probe error", host=host, error=str(e))
        return "no"

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log("info", "scanner starting",
        site=SCANNER_SITE, inventory=str(INVENTORY_FILE))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    inventory  = json.loads(INVENTORY_FILE.read_text())
    scan_start = datetime.now(timezone.utc)
    log("info", "inventory loaded", count=len(inventory))

    results = []

    for target in inventory:
        if shutdown_requested:
            log("warning", "shutdown mid-scan",
                probed=len(results),
                remaining=len(inventory) - len(results))
            break

        host = target["mgmt_ip"]
        log("info", "probing", host=host, name=target["name"])

        result = {
            "name":             target["name"],
            "mgmt_ip":          host,
            "site":             target["site"],
            "role":             target.get("role", ""),
            "ssh_accessible":   probe_ssh(host),
            "https_accessible": probe_https(host),
            "scanned_at":       datetime.now(timezone.utc).isoformat(),
        }
        results.append(result)
        log("info", "probe complete",
            host=host,
            ssh=result["ssh_accessible"],
            https=result["https_accessible"])

    # Always write — even partial results on shutdown
    scan_id     = scan_start.strftime("%Y%m%d-%H%M%S")
    output_file = OUTPUT_DIR / f"scan-{SCANNER_SITE}-{scan_id}.json"

    payload = {
        "scan_id":      scan_id,
        "scanner_site": SCANNER_SITE,
        "started_at":   scan_start.isoformat(),
        "ended_at":     datetime.now(timezone.utc).isoformat(),
        "partial":      shutdown_requested,
        "results":      results,
    }

    output_file.write_text(json.dumps(payload, indent=2))
    log("info", "scan complete",
        output=str(output_file),
        probed=len(results),
        partial=shutdown_requested)

    sys.exit(143 if shutdown_requested else 0)


if __name__ == "__main__":
    main()
