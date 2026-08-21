from __future__ import annotations

import argparse
import os

from ergo_crm.server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ergo-crm")
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve"],
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ERGO_CRM_PORT") or "8787"),
    )
    parser.add_argument(
        "--fail-nth-write",
        type=int,
        default=int(os.environ.get("ERGO_CRM_FAIL_NTH_WRITE") or "0"),
    )
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, fail_nth_write=args.fail_nth_write)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
