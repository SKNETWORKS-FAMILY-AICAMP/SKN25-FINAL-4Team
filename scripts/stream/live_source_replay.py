"""Service entrypoint for replaying PC1 harmonized source rows via FastAPI ingest.

This wrapper keeps the operational command name focused on the service role while
reusing the import-safe replay implementation in ``scripts.live``. Runtime POST
is blocked unless ``CMS_ENABLE_LIVE_SOURCE_REPLAY=1`` is set, so dry-run/help can
be executed safely in any environment.
"""

from __future__ import annotations

import os
import sys

from scripts.live.run_live_stream_injector import main as replay_main


def main() -> int:
    if "--runtime-post" in sys.argv and os.getenv("CMS_ENABLE_LIVE_SOURCE_REPLAY") != "1":
        raise SystemExit("runtime replay requires CMS_ENABLE_LIVE_SOURCE_REPLAY=1")
    replay_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
