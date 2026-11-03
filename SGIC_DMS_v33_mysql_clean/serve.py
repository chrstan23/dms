
"""SGIC DMS launcher (Waitress production server)."""
import os
import socket

from waitress import serve

import dbcompat
from app import LOG_DIR, VERSION, app, init_db

# Server configuration
HOST = "0.0.0.0"
PORT = int(os.environ.get("SGIC_PORT", "5000"))
THREADS = 8
SERVER_IP = "192.168.0.202"   # this host PC's fixed LAN address (shown to employees)

BASE = os.path.dirname(os.path.abspath(__file__))


def lan_addresses():
    """Return this computer's IPv4 addresses."""
    found = []

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass

    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass

    return found


def warnings():
    out = []

    if BASE.startswith("\\\\"):
        out.append(
            "The program is running from a NETWORK SHARE. "
            "Move it to a local folder such as C:\\SGIC_DMS."
        )

    if len(BASE) > 100:
        out.append(
            f"The program folder path is long ({len(BASE)} characters). "
            "Consider using C:\\SGIC_DMS."
        )

    return out


if __name__ == "__main__":
    init_db()

    print("=" * 66)
    print(f"SGIC Document Management System v{VERSION}")
    print(f"Listening on all network adapters, port {PORT}")
    ips = [SERVER_IP] + [i for i in lan_addresses() if i != SERVER_IP]
    if ips:
        print("Give employees ONE of these addresses (open it from another PC):")
        for ip in ips:
            print(f"    http://{ip}:{PORT}/")
    else:
        print("Could not detect the LAN IP - run ipconfig on this PC and use the IPv4 address.")
    print(f"On this PC only: http://127.0.0.1:{PORT}/   (never give 0.0.0.0 to employees)")
    print(f"Health check: http://127.0.0.1:{PORT}/healthz")
    print(f"Database: {dbcompat.describe()}")
    print(f"Log file: {os.path.join(LOG_DIR, 'dms.log')}")

    for warning in warnings():
        print("WARNING: " + warning)

    print("Keep this window open while employees use SGIC DMS.")
    print("Press Ctrl+C to stop the server.")
    print("=" * 66)

    serve(
        app,
        host=HOST,
        port=PORT,
        threads=THREADS,
        channel_timeout=120,
        ident="SGIC-DMS",
    )
