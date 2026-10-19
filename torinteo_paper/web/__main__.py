"""Run the owner application on a local machine or persistent-volume server."""
import argparse
import ipaddress
import os
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="Torinteo Markets owner paper desk")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data-dir", default="runtime/web")
    parser.add_argument("--static-dir", default="web/dist")
    parser.add_argument("--allow-host", action="append", default=[], help="Additional literal hostname/IP allowed at the HTTP boundary")
    parser.add_argument("--allow-origin", action="append", default=[], help="Explicit extra origin for a local development proxy")
    parser.add_argument("--trusted-proxy", action="append", default=[], help="Explicit proxy IP allowed to supply forwarded HTTPS/client headers")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    for value in args.trusted_proxy:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            parser.error("trusted proxy must be a literal IP address; wildcards are not accepted")
    try:
        import uvicorn
        from .app import create_app
    except ImportError:
        print("Install the web extra before starting the owner application: pip install -e '.[web]'", file=sys.stderr)
        return 2
    try:
        app = create_app(Path(args.data_dir), Path(args.static_dir), host=args.host,
                         token=os.environ.get("TORINTEO_WEB_TOKEN") or None,
                         allowed_hosts=args.allow_host, allowed_origins=args.allow_origin)
    except (ValueError, OSError) as exc:
        print(str(exc) if isinstance(exc, ValueError) else "Owner storage could not be opened.", file=sys.stderr)
        return 2
    # No proxy headers are trusted implicitly. A configured TLS proxy supplies
    # the HTTPS scheme so the app emits Secure cookies and verifies Origin.
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=bool(args.trusted_proxy),
                forwarded_allow_ips=",".join(args.trusted_proxy), access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
