from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any


def handler(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "message": "runpod smoke handler is running",
        "received_input": job.get("input", {}),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def _main() -> None:
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {"input": {}}
    print(json.dumps(handler(payload), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _main()
    else:
        import runpod

        runpod.serverless.start({"handler": handler})
