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
DEFAULT_CANDIDATE_DIR = PROJECT_ROOT / "artifacts" / "import_pmax_candidates"
CANDIDATE_DIR = Path(
    os.getenv("IMPORT_PMAX_CANDIDATES_ROOT", str(DEFAULT_CANDIDATE_DIR))
)
DEFAULT_LOG_DIR = Path(os.getenv("RUNPOD_JOB_LOG_DIR", "/tmp/runpod_job_logs"))
DEFAULT_TIMEOUT_SECONDS = int(
    os.getenv("RUNPOD_TRAIN_TIMEOUT_SECONDS", str(24 * 60 * 60))
)
DEFAULT_UPLOAD_TIMEOUT_SECONDS = int(
    os.getenv("RUNPOD_UPLOAD_TIMEOUT_SECONDS", str(60 * 60))
)
DEFAULT_UPLOAD_RETRIES = int(os.getenv("RUNPOD_UPLOAD_RETRIES", "3"))
MAX_LOG_TAIL_CHARS = int(os.getenv("RUNPOD_JOB_LOG_TAIL_CHARS", "12000"))
UPLOAD_CHUNK_SIZE = int(
    os.getenv("RUNPOD_UPLOAD_CHUNK_SIZE", str(1024 * 1024))
)
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
LOGICAL_METERS = {"V.Z81", "V.Z82", "H2.Z35x", "H2.Z36x"}
_ACTIVE_PROC: subprocess.Popen | None = None


class JobInputError(ValueError):
    pass


class JobStageError(RuntimeError):
    def __init__(
        self,
        stage: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.retryable = retryable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{uuid.uuid4().hex[:8]}"


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
    with path.open("rb") as source:
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(max(0, size - MAX_LOG_TAIL_CHARS))
        return source.read().decode(errors="replace")


def _as_list(value: Any, name: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise JobInputError(f"{name} must be a list of non-empty strings")
    return value


def _as_int(
    value: Any,
    name: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
) -> int | None:
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
        raise JobInputError(
            "run_id may contain only letters, digits, underscore, dash, and dot"
        )

    meters = _as_list(raw.get("meters"), "meters")
    if meters:
        invalid = sorted(set(meters) - LOGICAL_METERS)
        if invalid:
            raise JobInputError(f"invalid logical meters: {invalid}")

    upload_url = (
        raw.get("upload_url")
        or os.getenv("MODEL_ARTIFACT_UPLOAD_URL", "")
    ).strip()
    if not upload_url:
        raise JobInputError(
            "upload_url or MODEL_ARTIFACT_UPLOAD_URL is required"
        )
    parsed = urlparse(upload_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise JobInputError(
            "upload_url must be an absolute http:// or https:// URL"
        )

    allowed_hosts = {
        host.strip().lower()
        for host in os.getenv("RUNPOD_ALLOWED_UPLOAD_HOSTS", "").split(",")
        if host.strip()
    }
    allow_any_host = os.getenv("RUNPOD_ALLOW_ANY_UPLOAD_HOST", "0") == "1"
    if not allowed_hosts and not allow_any_host:
        raise JobInputError(
            "RUNPOD_ALLOWED_UPLOAD_HOSTS is required unless "
            "RUNPOD_ALLOW_ANY_UPLOAD_HOST=1"
        )
    if allowed_hosts and (parsed.hostname or "").lower() not in allowed_hosts:
        raise JobInputError(f"upload host is not allowed: {parsed.hostname}")

    if raw.get("upload_token") and os.getenv(
        "RUNPOD_ALLOW_INPUT_UPLOAD_TOKEN", "0"
    ) != "1":
        raise JobInputError(
            "upload_token job input is disabled; use ARTIFACT_UPLOAD_TOKEN"
        )
    upload_token = os.getenv("ARTIFACT_UPLOAD_TOKEN", "").strip()
    if (
        not upload_token
        and os.getenv("RUNPOD_ALLOW_INPUT_UPLOAD_TOKEN", "0") == "1"
    ):
        upload_token = str(raw.get("upload_token", "")).strip()
    if not upload_token:
        raise JobInputError("ARTIFACT_UPLOAD_TOKEN is required")

    return {
        "run_id": run_id,
        "meters": meters,
        "seed": _as_int(raw.get("seed"), "seed", minimum=0),
        "timeout_seconds": _as_int(
            raw.get("timeout_seconds"),
            "timeout_seconds",
            default=DEFAULT_TIMEOUT_SECONDS,
            minimum=60,
        ),
        "upload_timeout_seconds": _as_int(
            raw.get("upload_timeout_seconds"),
            "upload_timeout_seconds",
            default=DEFAULT_UPLOAD_TIMEOUT_SECONDS,
            minimum=10,
        ),
        "upload_retries": _as_int(
            raw.get("upload_retries"),
            "upload_retries",
            default=DEFAULT_UPLOAD_RETRIES,
            minimum=1,
        ),
        "upload_url": upload_url,
        "upload_token": upload_token,
        "overwrite_upload": _as_bool(
            raw.get("overwrite_upload"),
            "overwrite_upload",
            default=True,
        ),
        "overwrite_candidate": _as_bool(
            raw.get("overwrite_candidate"),
            "overwrite_candidate",
            default=False,
        ),
    }


def _build_train_command(
    request: dict[str, Any],
    candidate_dir: Path,
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "scripts.forecasting.train_import_pmax",
        "--device",
        "gpu",
        "--output-dir",
        str(candidate_dir),
        "--run-id",
        request["run_id"],
    ]
    if request["meters"]:
        command.extend(["--meters", *request["meters"]])
    if request["seed"] is not None:
        command.extend(["--seed", str(request["seed"])])
    return command


def _run_process_to_logs(
    command: list[str],
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: int,
) -> int:
    global _ACTIVE_PROC
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            stdout=stdout,
            stderr=stderr,
            preexec_fn=os.setsid,
        )
        _ACTIVE_PROC = process
        try:
            return process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            _kill_process_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                _kill_process_group(process, signal.SIGKILL)
                process.wait(timeout=10)
            raise
        finally:
            if _ACTIVE_PROC is process:
                _ACTIVE_PROC = None


def _assert_candidate_layout(candidate_dir: Path) -> int:
    summary = candidate_dir / "pmax_model_comparison_summary.csv"
    meter_root = candidate_dir / "input_24h" / "predict_60min"
    if not summary.is_file() or not meter_root.is_dir():
        raise JobStageError(
            "layout",
            f"candidate layout is incomplete: {candidate_dir}",
        )
    meter_count = len([path for path in meter_root.iterdir() if path.is_dir()])
    if meter_count < 1:
        raise JobStageError(
            "layout",
            f"candidate artifact has no meter directories: {meter_root}",
        )
    return meter_count


def _make_archive(run_id: str, candidate_dir: Path, log_dir: Path) -> Path:
    archive_path = log_dir / f"{run_id}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(candidate_dir, arcname=run_id)
    return archive_path


def _prepare_multipart(
    fields: list[tuple[str, str]],
    file_field: str,
    file_path: Path,
    boundary: str,
) -> tuple[int, list[bytes], bytes]:
    content_type = (
        mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    )
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
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
    )
    closing = f"\r\n--{boundary}--\r\n".encode()
    total = (
        sum(len(part) for part in parts)
        + file_path.stat().st_size
        + len(closing)
    )
    return total, parts, closing


def _upload_archive_once(
    request: dict[str, Any],
    archive_path: Path,
) -> dict[str, Any]:
    parsed = urlparse(request["upload_url"])
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    boundary = f"----runpod-pmax-{uuid.uuid4().hex}"
    content_length, parts, closing = _prepare_multipart(
        [
            ("run_id", request["run_id"]),
            ("overwrite", str(request["overwrite_upload"]).lower()),
        ],
        "file",
        archive_path,
        boundary,
    )
    connection_type = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(
        parsed.netloc,
        timeout=request["upload_timeout_seconds"],
    )
    try:
        connection.putrequest("POST", path, skip_host=True)
        connection.putheader("Host", parsed.netloc)
        connection.putheader("User-Agent", "import-pmax-runpod-job/1.0")
        connection.putheader(
            "Authorization",
            f"Bearer {request['upload_token']}",
        )
        connection.putheader(
            "Content-Type",
            f"multipart/form-data; boundary={boundary}",
        )
        connection.putheader("Content-Length", str(content_length))
        connection.endheaders()
        for part in parts:
            connection.send(part)
        with archive_path.open("rb") as source:
            while chunk := source.read(UPLOAD_CHUNK_SIZE):
                connection.send(chunk)
        connection.send(closing)
        response = connection.getresponse()
        body = response.read().decode(errors="replace")
        if response.status >= 300:
            retryable = response.status >= 500 or response.status == 429
            raise JobStageError(
                "upload",
                f"artifact upload failed: HTTP {response.status} {body[:1000]}",
                retryable=retryable,
            )
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw_response": body, "http_status": response.status}
    finally:
        connection.close()


def _upload_archive(
    request: dict[str, Any],
    archive_path: Path,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, request["upload_retries"] + 1):
        try:
            return _upload_archive_once(request, archive_path)
        except JobStageError as exc:
            last_error = exc
            if not exc.retryable or attempt >= request["upload_retries"]:
                raise
        except OSError as exc:
            last_error = exc
            if attempt >= request["upload_retries"]:
                raise JobStageError(
                    "upload",
                    f"artifact upload failed: {exc}",
                ) from exc
        time.sleep(min(30, attempt * 5))
    raise JobStageError("upload", f"artifact upload failed: {last_error}")


def _safe_error(request: dict[str, Any] | None, error: str) -> str:
    text = str(error)
    if request and request.get("upload_token"):
        text = text.replace(str(request["upload_token"]), "[REDACTED]")
    return text[:2000]


def _failure(
    request: dict[str, Any] | None,
    stage: str,
    error: str,
    started_at: str | None,
    stdout_path: Path | None,
    stderr_path: Path | None,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "run_id": request.get("run_id") if request else None,
        "stage": stage,
        "error": _safe_error(request, error),
        "started_at": started_at,
        "finished_at": _now_iso(),
        "stdout_path": str(stdout_path) if stdout_path else None,
        "stderr_path": str(stderr_path) if stderr_path else None,
        "stdout_tail": _tail(stdout_path),
        "stderr_tail": _tail(stderr_path),
    }


def handler(job: dict[str, Any]) -> dict[str, Any]:
    request: dict[str, Any] | None = None
    started_at: str | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    archive_path: Path | None = None
    try:
        request = _read_input(job)
        started_at = _now_iso()
        run_id = request["run_id"]
        candidate_dir = CANDIDATE_DIR / run_id
        log_dir = DEFAULT_LOG_DIR / run_id
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "train_stdout.log"
        stderr_path = log_dir / "train_stderr.log"

        if candidate_dir.exists() and not request["overwrite_candidate"]:
            raise JobStageError(
                "input",
                f"candidate run already exists on RunPod: {run_id}",
            )
        if candidate_dir.exists():
            shutil.rmtree(candidate_dir)
        candidate_dir.parent.mkdir(parents=True, exist_ok=True)

        command = _build_train_command(request, candidate_dir)
        try:
            returncode = _run_process_to_logs(
                command,
                stdout_path,
                stderr_path,
                request["timeout_seconds"],
            )
        except subprocess.TimeoutExpired as exc:
            raise JobStageError(
                "training",
                f"training timed out after {request['timeout_seconds']}s",
            ) from exc
        if returncode != 0:
            raise JobStageError(
                "training",
                f"training process failed with returncode={returncode}",
            )

        meter_count = _assert_candidate_layout(candidate_dir)
        archive_path = _make_archive(run_id, candidate_dir, log_dir)
        upload_result = _upload_archive(request, archive_path)
        result = {
            "status": "uploaded",
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": _now_iso(),
            "candidate_dir": str(candidate_dir),
            "meter_count": meter_count,
            "artifact_size_bytes": upload_result.get("size_bytes"),
            "upload": upload_result,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "stdout_tail": _tail(stdout_path),
            "stderr_tail": _tail(stderr_path),
        }
        return result
    except JobInputError as exc:
        return _failure(
            request, "input", str(exc), started_at, stdout_path, stderr_path
        )
    except JobStageError as exc:
        return _failure(
            request,
            exc.stage,
            str(exc),
            started_at,
            stdout_path,
            stderr_path,
        )
    except Exception as exc:
        return _failure(
            request,
            "unexpected",
            repr(exc),
            started_at,
            stdout_path,
            stderr_path,
        )
    finally:
        if (
            archive_path is not None
            and os.getenv("RUNPOD_KEEP_ARCHIVES", "0") != "1"
        ):
            archive_path.unlink(missing_ok=True)


def _load_local_job(args: argparse.Namespace) -> dict[str, Any]:
    if args.input_json:
        with Path(args.input_json).open() as source:
            payload = json.load(source)
    else:
        payload = json.loads(args.input)
    return payload if "input" in payload else {"id": "local", "input": payload}


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="RunPod Serverless local runner for Import P-Max"
    )
    parser.add_argument("--input-json")
    parser.add_argument("--input", default="{}")
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
