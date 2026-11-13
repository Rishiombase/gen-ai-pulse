"""Serve the site locally and keep it up to date automatically.

    python -m pipeline.serve                    # http://127.0.0.1:8080, refresh every 30 minutes
    python -m pipeline.serve --port 9000 --every 60

Runs the full pipeline once at start, then again on the interval, while serving site/.
"""
from __future__ import annotations

import argparse
import functools
import logging
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import config, run

log = logging.getLogger("serve")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep the console for pipeline progress
        pass

    def end_headers(self):
        if self.path.split("?")[0].endswith(".json"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def updater(every_min: float, extra_args: list[str]) -> None:
    """Run the pipeline on a wall-clock schedule.

    Checks every 20 seconds against real time (time.time), so an update that fell due
    while the computer was asleep runs right after it wakes instead of waiting out the
    remaining interval.
    """
    next_due = 0.0
    while True:
        if time.time() >= next_due:
            try:
                run.main(extra_args)
            except Exception:
                log.exception("update failed; will retry at the next interval")
            next_due = time.time() + every_min * 60
            log.info("next update at %s", time.strftime("%H:%M", time.localtime(next_due)))
        time.sleep(20)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--every", type=float, default=30, help="minutes between updates (default 30)")
    ap.add_argument("--no-embeddings", action="store_true")
    ap.add_argument("--keep-awake", action="store_true",
                    help="macOS: stop the computer from sleeping while the server runs (uses caffeinate)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

    extra = ["--no-embeddings"] if args.no_embeddings else []
    if args.keep_awake:
        import os
        import shutil
        import subprocess
        if shutil.which("caffeinate"):
            subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])  # ends when this process ends
            log.info("keeping the computer awake while serving")
        else:
            log.warning("--keep-awake needs macOS caffeinate; ignoring")
    threading.Thread(target=updater, args=(args.every, extra), daemon=True).start()
    handler = functools.partial(QuietHandler, directory=str(config.SITE_DIR))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    log.info("serving on http://%s:%d (updates every %g min; Ctrl+C to stop)", args.host, args.port, args.every)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
