from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = Path(os.getenv("MODEL_ARTIFACTS_DIR", str(PROJECT_ROOT / "artifacts"))).resolve()
CANDIDATE_DIR = Path(os.getenv("MODEL_CANDIDATE_DIR", str(ARTIFACTS_DIR / "candidate")))
DEFAULT_LOG_DIR = Path(os.getenv("RUNPOD_JOB_LOG_DIR", "/tmp/runpod_job_logs"))
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("RUNPOD_TRAIN_TIMEOUT_SECONDS", str(24 * 60 * 60)))
DEFAULT_UPLOAD_TIMEOUT_SECONDS = int(os.getenv("RUNPOD_UPLOAD_TIMEOUT_SECONDS", str(60 * 60)))
DEFAULT_UPLOAD_RETRIES = int(os.getenv("RUNPOD_UPLOAD_RETRIES", "3"))
MAX_LOG_TAIL_CHARS = int(os.getenv("RUNPOD_JOB_LOG_TAIL_CHARS", "12000"))
UPLOAD_CHUNK_SIZE = int(os.getenv("RUNPOD_UPLOAD_CHUNK_SIZE", str(1024 * 1024)))
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_ACTIVE_PROC: subprocess.Popen | None = None


class JobInputError(ValueError):
    """Invalid RunPod job input."""


class JobStageError(RuntimeError):
    """Failure with an explicit pipeline stage."""

    def __init__(self, stage: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.stage = stage
        self.retryable = retryable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_run_id() -> str:
    suffix = uuid.uuid4().hex[:8]
    return f"run_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{suffix}"


def _kill_process_group(proc: subprocess.Popen, sig: signal.Signals) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except ProcessLookupError:
        return


def _handle_sigterm(_signum: int, _frame: Any) -> None:
    if _ACTIVE_PROC is not None and _ACTIVE_PROC.poll() is None:
        _kill_process_group(_ACTIVE_PROC, signal.SIGTERM)
    raise SystemExit(143)


signal.signal(signal.SIGTERM, _handle_sigterm)


def _tail(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - MAX_LOG_TAIL_CHARS))
        return f.read().decode(errors="replace")


def _as_list(value: Any, name: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise JobInputError(f"{name} must be a list of non-empty strings")
    return value


def _as_int(value: Any, name: str, *, default: int | None = None, minimum: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise JobInputError(f"{name} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise JobInputError(f"{name} must be >= {minimum}")
    return parsed


def _as_bool(value: Any, name: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y"}:
            return True
        if lowered in {"0", "false", "no", "n"}:
            return False
    raise JobInputError(f"{name} must be a boolean")


def _read_input(job: dict[str, Any]) -> dict[str, Any]:
    raw = job.get("input", {})
    if not isinstance(raw, dict):
        raise JobInputError("job input must be an object")

    run_id = raw.get("run_id") or _new_run_id()
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise JobInputError("run_id may contain only letters, digits, underscore, dash, and dot")

    horizon = _as_int(raw.get("horizon"), "horizon", default=3)
    if horizon not in (1, 3):
        raise JobInputError("horizon must be 1 or 3")

    groups = _as_list(raw.get("groups"), "groups")
    if groups:
        bad = sorted(set(groups) - {"electric", "thermal"})
        if bad:
            raise JobInputError(f"invalid groups: {bad}")

    upload_url = (raw.get("upload_url") or os.getenv("MODEL_ARTIFACT_UPLOAD_URL", "")).strip()
    if not upload_url:
        raise JobInputError("upload_url or MODEL_ARTIFACT_UPLOAD_URL is required")
    parsed = urlparse(upload_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise JobInputError("upload_url must be an absolute http:// or https:// URL")

    allowed_hosts = {
        h.strip().lower()
        for h in os.getenv("RUNPOD_ALLOWED_UPLOAD_HOSTS", "").split(",")
        if h.strip()
    }
    allow_any_host = os.getenv("RUNPOD_ALLOW_ANY_UPLOAD_HOST", "0") == "1"
    if not allowed_hosts and not allow_any_host:
        raise JobInputError("RUNPOD_ALLOWED_UPLOAD_HOSTS is required unless RUNPOD_ALLOW_ANY_UPLOAD_HOST=1")
    if allowed_hosts and (parsed.hostname or "").lower() not in allowed_hosts:
        raise JobInputError(f"upload host is not allowed: {parsed.hostname}")

    if raw.get("upload_token") and os.getenv("RUNPOD_ALLOW_INPUT_UPLOAD_TOKEN", "0") != "1":
        raise JobInputError("upload_token job input is disabled; use ARTIFACT_UPLOAD_TOKEN")
    upload_token = os.getenv("ARTIFACT_UPLOAD_TOKEN", "").strip()
    if not upload_token and os.getenv("RUNPOD_ALLOW_INPUT_UPLOAD_TOKEN", "0") == "1":
        upload_token = str(raw.get("upload_token", "")).strip()
    if not upload_token:
        raise JobInputError("upload_token or ARTIFACT_UPLOAD_TOKEN is required")

    timeout_seconds = _as_int(
        raw.get("timeout_seconds"),
        "timeout_seconds",
        default=DEFAULT_TIMEOUT_SECONDS,
        minimum=60,
    )
    upload_timeout_seconds = _as_int(
        raw.get("upload_timeout_seconds"),
        "upload_timeout_seconds",
        default=DEFAULT_UPLOAD_TIMEOUT_SECONDS,
        minimum=10,
    )

    return {
        "run_id": run_id,
        "horizon": horizon,
        "upload_url": upload_url,
        "upload_token": upload_token,
        "overwrite_upload": _as_bool(raw.get("overwrite_upload"), "overwrite_upload", default=True),
        "overwrite_candidate": _as_bool(raw.get("overwrite_candidate"), "overwrite_candidate", default=False),
        "meters": _as_list(raw.get("meters"), "meters"),
        "groups": groups,
        "epochs": _as_int(raw.get("epochs"), "epochs", minimum=1),
        "batch_size": _as_int(raw.get("batch_size"), "batch_size", minimum=1),
        "seed": _as_int(raw.get("seed"), "seed", minimum=0),
        "timeout_seconds": timeout_seconds,
        "upload_timeout_seconds": upload_timeout_seconds,
        "upload_retries": _as_int(raw.get("upload_retries"), "upload_retries", default=DEFAULT_UPLOAD_RETRIES, minimum=1),
    }


def _build_train_command(req: dict[str, Any], candidate_dir: Path) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "energy_v84.train",
        "--horizon",
        str(req["horizon"]),
        "--output-dir",
        str(candidate_dir),
    ]
    if req["meters"]:
        cmd.extend(["--meters", *req["meters"]])
    if req["groups"]:
        cmd.extend(["--groups", *req["groups"]])
    if req["epochs"] is not None:
        cmd.extend(["--epochs", str(req["epochs"])])
    if req["batch_size"] is not None:
        cmd.extend(["--batch-size", str(req["batch_size"])])
    if req["seed"] is not None:
        cmd.extend(["--seed", str(req["seed"])])
    return cmd


def _run_process_to_logs(cmd: list[str], stdout_path: Path, stderr_path: Path, timeout_seconds: int) -> int:
    global _ACTIVE_PROC
    with stdout_path.open("w") as out, stderr_path.open("w") as err:
        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            text=True,
            stdout=out,
            stderr=err,
            preexec_fn=os.setsid,
        )
        _ACTIVE_PROC = proc
        try:
            return proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_process_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                _kill_process_group(proc, signal.SIGKILL)
                proc.wait(timeout=10)
            raise
        finally:
            if _ACTIVE_PROC is proc:
                _ACTIVE_PROC = None


def _assert_candidate_layout(candidate_dir: Path, horizon: int) -> int:
    summary_path = candidate_dir / f"train_summary_{horizon}h.csv"
    horizon_dir = candidate_dir / f"{horizon}h"
    if not summary_path.is_file() or not horizon_dir.is_dir():
        raise JobStageError("layout", f"candidate layout is incomplete: {candidate_dir}")
    meter_count = len([p for p in horizon_dir.iterdir() if p.is_dir()])
    if meter_count < 1:
        raise JobStageError("layout", f"candidate artifact has no meter directories: {horizon_dir}")
    return meter_count


def _make_archive(run_id: str, candidate_dir: Path, log_dir: Path) -> Path:
    archive_path = log_dir / f"{run_id}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(candidate_dir, arcname=run_id)
    return archive_path


def _prepare_multipart(fields: list[tuple[str, str]], file_field: str, file_path: Path, boundary: str) -> tuple[int, list[bytes], bytes]:
    file_name = file_path.name
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
    )
    closing = f"\r\n--{boundary}--\r\n".encode()
    total = sum(len(part) for part in parts) + file_path.stat().st_size + len(closing)
    return total, parts, closing


def _upload_archive_once(req: dict[str, Any], archive_path: Path) -> dict[str, Any]:
    parsed = urlparse(req["upload_url"])
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    fields = [
        ("run_id", req["run_id"]),
        ("horizon", str(req["horizon"])),
        ("overwrite", str(req["overwrite_upload"]).lower()),
    ]
    boundary = f"----runpod-artifact-{uuid.uuid4().hex}"
    content_length, parts, closing = _prepare_multipart(fields, "file", archive_path, boundary)

    conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parsed.netloc, timeout=req["upload_timeout_seconds"])
    try:
        conn.putrequest("POST", path, skip_host=True)
        conn.putheader("Host", parsed.netloc)
        conn.putheader("User-Agent", "energy-runpod-job/1.0")
        conn.putheader("Authorization", f"Bearer {req['upload_token']}")
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(content_length))
        conn.endheaders()

        for part in parts:
            conn.send(part)
        with archive_path.open("rb") as f:
            while True:
                chunk = f.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                conn.send(chunk)
        conn.send(closing)

        response = conn.getresponse()
        body = response.read().decode(errors="replace")
        if response.status >= 300:
            retryable = response.status >= 500 or response.status == 429
            body_preview = body[:1000]
            raise JobStageError("upload", f"artifact upload failed: HTTP {response.status} {body_preview}", retryable=retryable)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw_response": body, "http_status": response.status}
    finally:
        conn.close()


def _upload_archive(req: dict[str, Any], archive_path: Path) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, req["upload_retries"] + 1):
        try:
            return _upload_archive_once(req, archive_path)
        except JobStageError as exc:
            last_error = exc
            if not exc.retryable or attempt >= req["upload_retries"]:
                raise
        except OSError as exc:
            last_error = exc
            if attempt >= req["upload_retries"]:
                raise JobStageError("upload", f"artifact upload failed: {exc}") from exc
        time.sleep(min(30, attempt * 5))
    raise JobStageError("upload", f"artifact upload failed: {last_error}")


def _success(req: dict[str, Any], candidate_dir: Path, archive_path: Path, upload_result: dict[str, Any], meter_count: int, started_at: str, stdout_path: Path, stderr_path: Path) -> dict[str, Any]:
    if os.getenv("RUNPOD_KEEP_ARCHIVES", "0") != "1":
        archive_path.unlink(missing_ok=True)
        archive_value: str | None = None
    else:
        archive_value = str(archive_path)
    return {
        "status": "uploaded",
        "run_id": req["run_id"],
        "horizon": req["horizon"],
        "started_at": started_at,
        "finished_at": _now_iso(),
        "candidate_dir": str(candidate_dir),
        "archive_path": archive_value,
        "artifact_size_bytes": upload_result.get("size_bytes"),
        "meter_count": meter_count,
        "upload": upload_result,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_tail": _tail(stdout_path),
        "stderr_tail": _tail(stderr_path),
    }


def _safe_error(req: dict[str, Any] | None, error: str) -> str:
    text = str(error)
    if req and req.get("upload_token"):
        text = text.replace(str(req["upload_token"]), "[REDACTED]")
    return text[:2000]


def _failure(req: dict[str, Any] | None, stage: str, error: str, started_at: str | None, stdout_path: Path | None, stderr_path: Path | None) -> dict[str, Any]:
    return {
        "status": "failed",
        "run_id": req.get("run_id") if req else None,
        "horizon": req.get("horizon") if req else None,
        "stage": stage,
        "error": _safe_error(req, error),
        "started_at": started_at,
        "finished_at": _now_iso(),
        "stdout_path": str(stdout_path) if stdout_path else None,
        "stderr_path": str(stderr_path) if stderr_path else None,
        "stdout_tail": _tail(stdout_path),
        "stderr_tail": _tail(stderr_path),
    }


def handler(job: dict[str, Any]) -> dict[str, Any]:
    started_at: str | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    archive_path: Path | None = None
    req: dict[str, Any] | None = None
    try:
        req = _read_input(job)
        started_at = _now_iso()
        run_id = req["run_id"]
        candidate_dir = CANDIDATE_DIR / run_id
        log_dir = DEFAULT_LOG_DIR / run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "train_stdout.log"
        stderr_path = log_dir / "train_stderr.log"

        if candidate_dir.exists() and not req["overwrite_candidate"]:
            raise JobStageError("input", f"candidate run already exists on RunPod: {run_id}")
        if candidate_dir.exists() and req["overwrite_candidate"]:
            shutil.rmtree(candidate_dir)
        candidate_dir.parent.mkdir(parents=True, exist_ok=True)

        cmd = _build_train_command(req, candidate_dir)
        try:
            returncode = _run_process_to_logs(cmd, stdout_path, stderr_path, req["timeout_seconds"])
        except subprocess.TimeoutExpired as exc:
            raise JobStageError("training", f"training timed out after {req['timeout_seconds']}s") from exc
        if returncode != 0:
            raise JobStageError("training", f"training process failed with returncode={returncode}")

        meter_count = _assert_candidate_layout(candidate_dir, req["horizon"])
        archive_path = _make_archive(run_id, candidate_dir, log_dir)
        upload_result = _upload_archive(req, archive_path)
        return _success(req, candidate_dir, archive_path, upload_result, meter_count, started_at, stdout_path, stderr_path)
    except JobInputError as exc:
        return _failure(req, "input", str(exc), started_at, stdout_path, stderr_path)
    except JobStageError as exc:
        return _failure(req, exc.stage, str(exc), started_at, stdout_path, stderr_path)
    except Exception as exc:
        return _failure(req, "unexpected", repr(exc), started_at, stdout_path, stderr_path)
    finally:
        if archive_path is not None and os.getenv("RUNPOD_KEEP_ARCHIVES", "0") != "1":
            archive_path.unlink(missing_ok=True)


def _load_local_job(args: argparse.Namespace) -> dict[str, Any]:
    if args.input_json:
        with Path(args.input_json).open() as f:
            payload = json.load(f)
    else:
        payload = json.loads(args.input)
    if "input" in payload:
        return payload
    return {"id": "local", "input": payload}


def _main() -> None:
    parser = argparse.ArgumentParser(description="RunPod Serverless local runner")
    parser.add_argument("--input-json", help="Path to a JSON job or input object for local testing.")
    parser.add_argument("--input", default="{}", help="JSON job or input object for local testing.")
    args = parser.parse_args()
    result = handler(_load_local_job(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _main()
    else:
        import runpod

        runpod.serverless.start({"handler": handler})
