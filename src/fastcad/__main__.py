from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="fastcad")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("FASTCAD_HOST", args.host)
    os.environ.setdefault("FASTCAD_PORT", str(args.port))

    # Use the modern sans-io websockets implementation. The legacy
    # `websockets` driver races between its keepalive_ping coroutine
    # and our progress-event sends during long agent turns, tripping
    # an assertion in `_drain_helper` and severing the connection
    # mid-turn (observed in tmp/feedback/20260502T073401604186/).
    # The sans-io path doesn't go through that code at all.
    uvicorn.run(
        "fastcad.server.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
        ws="websockets-sansio",
    )


if __name__ == "__main__":
    main()
