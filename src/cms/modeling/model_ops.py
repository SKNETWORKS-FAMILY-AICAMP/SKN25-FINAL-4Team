from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
MODEL_KINDS = {"pmax", "anomaly"}
TERMINAL_RUNPOD_STATUSES = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}
SENSITIVE_KEYS = {
    "authorization",
    "token",
    "api_key",
    "upload_token",
    "upload_url",
    "training_data_url",
    "artifact_upload_token",
    "runpod_api_key",
}
PMAX_SUMMARY_FILENAME = "pmax_model_comparison_summary.csv"
MODEL_OPS_VALIDATION_FILENAME = "model_ops_validation.json"


class ModelOpsError(RuntimeError):
    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ModelOpsPaths:
    model_kind: str
    artifacts_root: Path
    candidates_root: Path
    deployed_root: Path
    archives_root: Path
    incoming_root: Path
    training_jobs_root: Path


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return env if env is not None else os.environ


def validate_model_kind(model_kind: str) -> str:
    if model_kind not in MODEL_KINDS:
        raise ModelOpsError(404, f"unsupported model_kind: {model_kind}")
    return model_kind


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ModelOpsError(400, "run_id must use only letters, numbers, dot, underscore, or hyphen")
    return run_id


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ModelOpsError(400, "invalid RunPod job_id")
    return job_id


def new_run_id() -> str:
    import secrets

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{secrets.token_hex(4)}"


def paths_for(model_kind: str, env: Mapping[str, str] | None = None) -> ModelOpsPaths:
    values = _env(env)
    validate_model_kind(model_kind)
    if model_kind == "pmax":
        root = Path(values.get("IMPORT_PMAX_ARTIFACTS_DIR", str(PROJECT_ROOT / "artifacts" / "pmax"))).resolve()
        return ModelOpsPaths(
            model_kind=model_kind,
            artifacts_root=root,
            candidates_root=Path(values.get("IMPORT_PMAX_CANDIDATES_ROOT", str(root / "import_pmax_candidates"))).resolve(),
            deployed_root=Path(values.get("IMPORT_PMAX_DEPLOYED_ROOT", str(root / "import_pmax_v29_60min"))).resolve(),
            archives_root=Path(values.get("IMPORT_PMAX_ARCHIVES_ROOT", str(root / "import_pmax_archives"))).resolve(),
            incoming_root=Path(values.get("IMPORT_PMAX_INCOMING_ROOT", str(root / "import_pmax_incoming"))).resolve(),
            training_jobs_root=Path(values.get("IMPORT_PMAX_TRAINING_JOBS_ROOT", str(root / "import_pmax_training_jobs"))).resolve(),
        )

    root = Path(
        values.get(
            "ANOMALY_ARTIFACTS_DIR",
            values.get("MODEL_ARTIFACTS_DIR", str(PROJECT_ROOT / "artifacts" / "anomaly")),
        )
    ).resolve()
    return ModelOpsPaths(
        model_kind=model_kind,
        artifacts_root=root,
        candidates_root=Path(values.get("ANOMALY_CANDIDATES_ROOT", str(root.parent / "anomaly_candidates"))).resolve(),
        deployed_root=Path(values.get("ANOMALY_DEPLOYED_ROOT", str(root))).resolve(),
        archives_root=Path(values.get("ANOMALY_ARCHIVES_ROOT", str(root.parent / "anomaly_archives"))).resolve(),
        incoming_root=Path(values.get("ANOMALY_INCOMING_ROOT", str(root.parent / "anomaly_incoming"))).resolve(),
        training_jobs_root=Path(values.get("ANOMALY_TRAINING_JOBS_ROOT", str(root.parent / "anomaly_training_jobs"))).resolve(),
    )


def training_data_root_for(model_kind: str, env: Mapping[str, str] | None = None) -> Path:
    validate_model_kind(model_kind)
    values = _env(env)
    paths = paths_for(model_kind, values)
    if model_kind == "anomaly":
        return Path(values.get("ANOMALY_TRAINING_DATA_ROOT", str(paths.artifacts_root.parent / "anomaly_training_data"))).resolve()
    return Path(values.get("IMPORT_PMAX_TRAINING_DATA_ROOT", str(paths.artifacts_root / "import_pmax_training_data"))).resolve()


def training_data_archive_path(model_kind: str, run_id: str, env: Mapping[str, str] | None = None) -> Path:
    validate_run_id(run_id)
    return training_data_root_for(model_kind, env) / f"{run_id}.tar.gz"


def _artifact_upload_url(model_kind: str | None, env: Mapping[str, str]) -> str:
    if model_kind == "pmax":
        specific = (env.get("RUNPOD_PMAX_ARTIFACT_UPLOAD_URL") or "").strip()
    elif model_kind == "anomaly":
        specific = (env.get("RUNPOD_ANOMALY_ARTIFACT_UPLOAD_URL") or "").strip()
    else:
        specific = ""
    return specific or (env.get("RUNPOD_ARTIFACT_UPLOAD_URL") or env.get("MODEL_ARTIFACT_UPLOAD_URL") or "").strip()


def _runpod_public_base_url(env: Mapping[str, str], model_kind: str | None = None) -> str:
    if model_kind == "pmax":
        raw = (env.get("RUNPOD_PMAX_TRAINING_DATA_BASE_URL") or env.get("RUNPOD_TRAINING_DATA_BASE_URL") or "").strip()
    elif model_kind == "anomaly":
        raw = (env.get("RUNPOD_ANOMALY_TRAINING_DATA_BASE_URL") or env.get("RUNPOD_TRAINING_DATA_BASE_URL") or "").strip()
    else:
        raw = (env.get("RUNPOD_TRAINING_DATA_BASE_URL") or "").strip()
    if raw:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ModelOpsError(500, "RUNPOD_TRAINING_DATA_BASE_URL must be an absolute URL")
        return raw.rstrip("/")

    upload_url = _artifact_upload_url(model_kind, env)
    if not upload_url:
        raise ModelOpsError(500, "RUNPOD_TRAINING_DATA_BASE_URL or RUNPOD_ARTIFACT_UPLOAD_URL is required")
    parsed = urllib.parse.urlparse(upload_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelOpsError(500, "artifact upload URL must be an absolute URL")
    return f"{parsed.scheme}://{parsed.netloc}"


def _maybe_export_training_data(model_kind: str, runpod_input: dict[str, Any], payload: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any] | None:
    if not truthy(str(payload.get("export_training_data", "1"))):
        return None

    if model_kind == "anomaly":
        from cms.modeling.anomaly.export_training_data import export_training_data_archive

        training_data = export_training_data_archive(
            training_data_root_for(model_kind, env),
            runpod_input['run_id'],
            meters=payload.get("meters"),
            groups=payload.get("groups"),
            overwrite=bool(payload.get("overwrite_training_data", False)),
        )
    elif model_kind == "pmax":
        from cms.modeling.pmax.export_training_data import export_training_data_archive

        training_data = export_training_data_archive(
            training_data_root_for(model_kind, env),
            runpod_input['run_id'],
            meters=payload.get("meters"),
            table_name=payload.get("table"),
            overwrite=bool(payload.get("overwrite_training_data", False)),
        )
    else:
        return None
    runpod_input["training_data_url"] = f"{_runpod_public_base_url(env, model_kind)}/model-ops/{model_kind}/training/data/{runpod_input['run_id']}"
    return training_data


def require_model_ops_token(authorization: str | None, env: Mapping[str, str] | None = None) -> None:
    values = _env(env)
    expected = (values.get("MODEL_OPS_TOKEN") or values.get("ARTIFACT_UPLOAD_TOKEN") or "").strip()
    if not expected:
        raise ModelOpsError(503, "MODEL_OPS_TOKEN or ARTIFACT_UPLOAD_TOKEN is not configured")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ModelOpsError(401, "Bearer token required")
    import secrets

    if not secrets.compare_digest(token, expected):
        raise ModelOpsError(403, "Invalid token")


def require_env_gate(env_name: str, purpose: str, env: Mapping[str, str] | None = None) -> None:
    if not truthy(_env(env).get(env_name)):
        raise ModelOpsError(403, f"{purpose} is disabled; set {env_name}=1 to enable")


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def _required_env(name: str, env: Mapping[str, str]) -> str:
    value = (env.get(name) or "").strip()
    if not value:
        raise ModelOpsError(500, f"{name} is not configured")
    return value


def _runpod_endpoint_id(model_kind: str, env: Mapping[str, str]) -> str:
    validate_model_kind(model_kind)
    specific_name = "RUNPOD_PMAX_ENDPOINT_ID" if model_kind == "pmax" else "RUNPOD_ANOMALY_ENDPOINT_ID"
    return (env.get(specific_name) or "").strip() or _required_env("RUNPOD_ENDPOINT_ID", env)


def _runpod_timeout(env: Mapping[str, str]) -> int:
    raw = env.get("RUNPOD_API_TIMEOUT_SECONDS")
    if raw is None:
        return 60
    try:
        value = int(raw)
    except ValueError as exc:
        raise ModelOpsError(500, "RUNPOD_API_TIMEOUT_SECONDS must be an integer") from exc
    if value <= 0:
        raise ModelOpsError(500, "RUNPOD_API_TIMEOUT_SECONDS must be positive")
    return value


def call_runpod(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    model_kind: str | None = None,
    endpoint_id: str | None = None,
) -> dict[str, Any]:
    values = _env(env)
    require_env_gate("CMS_MODEL_OPS_ENABLE_RUNPOD", "RunPod network calls", values)
    endpoint = endpoint_id or (_runpod_endpoint_id(model_kind, values) if model_kind else _required_env("RUNPOD_ENDPOINT_ID", values))
    request = urllib.request.Request(
        f"https://api.runpod.ai/v2/{endpoint}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {_required_env('RUNPOD_API_KEY', values)}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_runpod_timeout(values)) as response:
            body = response.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise ModelOpsError(502, {"runpod_status": exc.code, "body": body[:2000]}) from exc
    except urllib.error.URLError as exc:
        raise ModelOpsError(502, f"RunPod API request failed: {exc}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ModelOpsError(502, f"RunPod API returned non-JSON response: {body[:1000]}") from exc


def _job_path(model_kind: str, job_id: str, env: Mapping[str, str] | None = None) -> Path:
    return paths_for(model_kind, env).training_jobs_root / f"{validate_job_id(job_id)}.json"


def _write_job_record(record: dict[str, Any], env: Mapping[str, str] | None = None) -> None:
    require_env_gate("CMS_MODEL_OPS_ENABLE_STATE_WRITES", "local model-ops state writes", env)
    root = paths_for(record["model_kind"], env).training_jobs_root
    root.mkdir(parents=True, exist_ok=True)
    path = _job_path(record["model_kind"], record["job_id"], env)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_job_record(model_kind: str, job_id: str, env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    path = _job_path(model_kind, job_id, env)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ModelOpsError(500, f"training job record parse failed: {exc}") from exc


def latest_job_record(model_kind: str, env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    root = paths_for(model_kind, env).training_jobs_root
    if not root.exists():
        return None
    records: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return max(records, key=lambda item: item.get("created_at") or "") if records else None


def build_runpod_input(model_kind: str, payload: Mapping[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    validate_model_kind(model_kind)
    run_id = payload.get("run_id") or new_run_id()
    if not isinstance(run_id, str):
        raise ModelOpsError(400, "run_id must be a string")
    validate_run_id(run_id)
    request: dict[str, Any] = {
        "model_kind": model_kind,
        "run_id": run_id,
        "overwrite_upload": bool(payload.get("overwrite_upload", True)),
        "overwrite_candidate": bool(payload.get("overwrite_candidate", False)),
    }
    for key in ("meters", "groups"):
        if payload.get(key) is not None:
            value = payload[key]
            if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
                raise ModelOpsError(400, f"{key} must be a list of non-empty strings")
            request[key] = value
    for key in ("horizon", "epochs", "batch_size", "seed", "timeout_seconds", "upload_timeout_seconds", "upload_retries"):
        if payload.get(key) is not None:
            try:
                request[key] = int(payload[key])
            except (TypeError, ValueError) as exc:
                raise ModelOpsError(400, f"{key} must be an integer") from exc
    if model_kind == "anomaly" and request.get("horizon") not in (1, 3):
        request["horizon"] = 3 if "horizon" not in request else request["horizon"]
        if request["horizon"] not in (1, 3):
            raise ModelOpsError(400, "anomaly horizon must be 1 or 3")
    upload_url = _artifact_upload_url(model_kind, _env(env))
    if upload_url:
        request["upload_url"] = upload_url
    return request


def start_training(model_kind: str, payload: Mapping[str, Any], env: Mapping[str, str] | None = None) -> dict[str, Any]:
    values = _env(env)
    require_env_gate("CMS_MODEL_OPS_ENABLE_RUNPOD", "RunPod training submission", values)
    require_env_gate("CMS_MODEL_OPS_ENABLE_STATE_WRITES", "local training job records", values)
    runpod_input = build_runpod_input(model_kind, payload, values)
    endpoint_id = _runpod_endpoint_id(model_kind, values)
    try:
        training_data = _maybe_export_training_data(model_kind, runpod_input, payload, values)
    except Exception as exc:
        raise ModelOpsError(500, f"training data export failed: {type(exc).__name__}: {exc}") from exc
    response = call_runpod("POST", "/run", {"input": runpod_input}, values, endpoint_id=endpoint_id)
    job_id = response.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise ModelOpsError(502, {"message": "RunPod response does not include job id", "response": sanitize(response)})
    validate_job_id(job_id)
    now = utc_now_iso()
    record = {
        "model_kind": model_kind,
        "job_id": job_id,
        "run_id": runpod_input['run_id'],
        "horizon": runpod_input.get("horizon"),
        "status": response.get("status"),
        "created_at": now,
        "updated_at": now,
        "runpod_endpoint_id": endpoint_id,
        "request_input": sanitize(runpod_input),
        "runpod_response": sanitize(response),
        "training_data": training_data,
        "next_action": "wait",
    }
    _write_job_record(record, values)
    return {
        "status": "submitted",
        "model_kind": model_kind,
        "job_id": job_id,
        "run_id": runpod_input['run_id'],
        "horizon": runpod_input.get("horizon"),
        "runpod_status": response.get("status"),
        "training_data": training_data,
        "next_action": "wait",
    }


def _next_action(record: dict[str, Any]) -> str:
    response = record.get("runpod_status_response")
    if isinstance(response, dict) and isinstance(response.get("output"), dict):
        output = response["output"]
        if output.get("status") == "uploaded" and output.get("upload"):
            return "validate"
        if output.get("status") == "failed":
            return "inspect_failure"
    if record.get("status") in TERMINAL_RUNPOD_STATUSES:
        return "inspect_result"
    return "wait"


def training_status(model_kind: str, job_id: str | None = None, *, latest: bool = False, refresh: bool = True, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    validate_model_kind(model_kind)
    if latest:
        record = latest_job_record(model_kind, env)
        if record is None:
            raise ModelOpsError(404, "training job record not found")
    else:
        if job_id is None:
            raise ModelOpsError(400, "job_id is required")
        record = read_job_record(model_kind, job_id, env)
        if record is None:
            raise ModelOpsError(404, "training job was not submitted from this server")
    if refresh and record.get("status") not in TERMINAL_RUNPOD_STATUSES and truthy(_env(env).get("CMS_MODEL_OPS_ENABLE_RUNPOD")):
        response = sanitize(
            call_runpod(
                "GET",
                f"/status/{record['job_id']}",
                env=env,
                model_kind=model_kind,
                endpoint_id=record.get("runpod_endpoint_id"),
            )
        )
        record.update({"status": response.get("status"), "updated_at": utc_now_iso(), "runpod_status_response": response})
        if isinstance(response.get("output"), dict):
            output = response["output"]
            record["run_id"] = record.get("run_id") or output.get("run_id")
            record["horizon"] = record.get("horizon") or output.get("horizon")
            record["upload"] = output.get("upload")
        record["next_action"] = _next_action(record)
        _write_job_record(record, env)
    else:
        record.setdefault("refresh_skipped", True)
    return record


def _safe_extract_tar(archive_path: Path, destination: Path) -> None:
    with tarfile.open(archive_path) as archive:
        dest = destination.resolve()
        members = archive.getmembers()
        for member in members:
            target = (destination / member.name).resolve()
            if target != dest and dest not in target.parents:
                raise ModelOpsError(400, f"Unsafe tar member: {member.name}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ModelOpsError(400, f"Archive member type is not allowed: {member.name}")
            if not (member.isdir() or member.isfile()):
                raise ModelOpsError(400, f"Unsupported tar member: {member.name}")
        for member in members:
            target = (destination / member.name).resolve()
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = archive.extractfile(member)
            if source is None:
                raise ModelOpsError(400, f"Cannot read tar member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        dest = destination.resolve()
        infos = archive.infolist()
        for info in infos:
            target = (destination / info.filename).resolve()
            if target != dest and dest not in target.parents:
                raise ModelOpsError(400, f"Unsafe zip member: {info.filename}")
            file_type = (info.external_attr >> 16) & 0o170000
            if file_type == 0o120000 or file_type not in (0, 0o040000, 0o100000):
                raise ModelOpsError(400, f"Unsupported zip member: {info.filename}")
        for info in infos:
            target = (destination / info.filename).resolve()
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def extract_archive(archive_path: Path, destination: Path) -> None:
    suffixes = "".join(archive_path.suffixes).lower()
    if suffixes.endswith((".tar.gz", ".tgz", ".tar")):
        _safe_extract_tar(archive_path, destination)
    elif suffixes.endswith(".zip"):
        _safe_extract_zip(archive_path, destination)
    else:
        raise ModelOpsError(400, "Only .tar, .tar.gz, .tgz, or .zip archives are supported")


def _candidate_layout_ok(model_kind: str, candidate_root: Path, horizon: int | None = None) -> bool:
    if model_kind == "pmax":
        return (candidate_root / "input_24h" / "predict_60min").is_dir() and (candidate_root / PMAX_SUMMARY_FILENAME).is_file()
    if horizon not in (1, 3):
        return False
    return (candidate_root / f"{horizon}h").is_dir() and (candidate_root / f"train_summary_{horizon}h.csv").is_file()


def _find_candidate_root(extract_dir: Path, model_kind: str, run_id: str, horizon: int | None = None) -> Path:
    candidates = [extract_dir / run_id, extract_dir / "candidate" / run_id, extract_dir]
    for candidate in candidates:
        if _candidate_layout_ok(model_kind, candidate, horizon):
            return candidate
    if model_kind == "pmax":
        detail = f"Archive does not contain input_24h/predict_60min/ and {PMAX_SUMMARY_FILENAME} for run_id={run_id}"
    else:
        detail = f"Archive does not contain {horizon}h/ and train_summary_{horizon}h.csv for run_id={run_id}"
    raise ModelOpsError(400, detail)


def install_candidate_archive(model_kind: str, run_id: str, archive_path: Path, *, horizon: int | None = None, overwrite: bool = False, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    require_env_gate("CMS_MODEL_OPS_ENABLE_ARTIFACT_WRITES", "artifact upload writes", env)
    validate_model_kind(model_kind)
    validate_run_id(run_id)
    if model_kind == "anomaly" and horizon not in (1, 3):
        raise ModelOpsError(400, "anomaly horizon must be 1 or 3")
    ops_paths = paths_for(model_kind, env)
    target_dir = ops_paths.candidates_root / run_id
    if target_dir.exists() and not overwrite:
        raise ModelOpsError(409, f"candidate run already exists: {run_id}")
    ops_paths.candidates_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"artifact_{run_id}_", dir=str(ops_paths.incoming_root if ops_paths.incoming_root.exists() else None)) as temporary:
        extract_dir = Path(temporary)
        extract_archive(archive_path, extract_dir)
        source_root = _find_candidate_root(extract_dir, model_kind, run_id, horizon)
        staged_target = ops_paths.candidates_root / f".{run_id}.uploading"
        if staged_target.exists():
            shutil.rmtree(staged_target)
        shutil.copytree(source_root, staged_target)
        if target_dir.exists():
            shutil.rmtree(target_dir)
        staged_target.rename(target_dir)
    return candidate_status(model_kind, run_id, horizon=horizon, env=env) | {"status": "uploaded", "next_action": "validate"}


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _csv_preview(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            rows = list(reader)
    except Exception as exc:
        return {"error": str(exc)}
    return {
        "rows": len(rows),
        "columns": reader.fieldnames or [],
        "first_row": rows[0] if rows else None,
    }


def candidate_status(model_kind: str, run_id: str, *, horizon: int | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    validate_model_kind(model_kind)
    validate_run_id(run_id)
    ops_paths = paths_for(model_kind, env)
    candidate_root = ops_paths.candidates_root / run_id
    if model_kind == "pmax":
        summary_path = candidate_root / PMAX_SUMMARY_FILENAME
        layout = _candidate_layout_ok(model_kind, candidate_root)
        meter_root = candidate_root / "input_24h" / "predict_60min"
    else:
        if horizon not in (1, 3):
            raise ModelOpsError(400, "anomaly horizon must be 1 or 3")
        summary_path = candidate_root / f"train_summary_{horizon}h.csv"
        layout = _candidate_layout_ok(model_kind, candidate_root, horizon)
        meter_root = candidate_root / f"{horizon}h"
    return {
        "model_kind": model_kind,
        "run_id": run_id,
        "horizon": horizon,
        "candidate_exists": candidate_root.is_dir(),
        "candidate_path": _relative_or_absolute(candidate_root),
        "layout_ok": layout,
        "meter_dir_count": len([path for path in meter_root.iterdir() if path.is_dir()]) if meter_root.is_dir() else 0,
        "summary": _csv_preview(summary_path),
        "validation": read_model_ops_validation(candidate_root),
    }


def _digest_tree(root: Path) -> str:
    if not root.is_dir():
        raise ModelOpsError(404, f"candidate not found: {root}")
    digest = hashlib.sha256()
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def read_model_ops_validation(candidate_root: Path) -> dict[str, Any] | None:
    path = candidate_root / MODEL_OPS_VALIDATION_FILENAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


def validate_candidate(model_kind: str, run_id: str, *, horizon: int | None = None, write_result: bool = False, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    if write_result:
        require_env_gate("CMS_MODEL_OPS_ENABLE_ARTIFACT_WRITES", "validation marker writes", env)
    status = candidate_status(model_kind, run_id, horizon=horizon, env=env)
    if not status["candidate_exists"]:
        raise ModelOpsError(404, f"candidate run not found: {run_id}")
    if not status["layout_ok"]:
        raise ModelOpsError(400, "candidate artifact layout is incomplete")
    ops_paths = paths_for(model_kind, env)
    candidate_root = ops_paths.candidates_root / run_id

    if model_kind == "pmax" and truthy(_env(env).get("CMS_MODEL_OPS_ENABLE_STRICT_VALIDATION")):
        from cms.modeling.pmax import validation as pmax_validation

        return pmax_validation.validate_candidate(
            candidate_root,
            ops_paths.deployed_root,
            run_id=run_id,
            write_result=write_result,
        )

    if model_kind == "anomaly":
        if horizon not in (1, 3):
            raise ModelOpsError(400, "anomaly horizon must be 1 or 3")
        from cms.modeling.anomaly import validation as anomaly_validation

        try:
            return anomaly_validation.validate_candidate(
                candidate_root,
                ops_paths.deployed_root,
                run_id=run_id,
                horizon=horizon,
                write_result=write_result,
            )
        except Exception as exc:
            raise ModelOpsError(500, f"Anomaly validation failed: {type(exc).__name__}: {exc}") from exc

    payload = {
        "model_kind": model_kind,
        "run_id": run_id,
        "horizon": horizon,
        "result": "pass",
        "validated_at": utc_now_iso(),
        "candidate_root": str(candidate_root),
        "deployed_root": str(ops_paths.deployed_root),
        "candidate_digest": _digest_tree(candidate_root),
        "strict_validation": False,
        "summary": status["summary"],
        "meter_dir_count": status["meter_dir_count"],
    }
    if write_result:
        (candidate_root / MODEL_OPS_VALIDATION_FILENAME).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def _assert_model_ops_validation_current(candidate_root: Path, *, allow_warn: bool = False) -> dict[str, Any]:
    marker = read_model_ops_validation(candidate_root)
    if marker is None:
        raise ModelOpsError(400, f"{MODEL_OPS_VALIDATION_FILENAME} not found. Validate the candidate first.")
    if marker.get("result") == "warn" and not allow_warn:
        raise ModelOpsError(400, "candidate validation result is warn; set allow_warn=true to promote")
    if marker.get("result") not in {"pass", "warn"}:
        raise ModelOpsError(400, f"invalid validation result: {marker.get('result')}")
    if marker.get("candidate_digest") != _digest_tree(candidate_root):
        raise ModelOpsError(400, "candidate artifacts changed after validation; validate again")
    return marker


def promote_candidate(model_kind: str, run_id: str, *, horizon: int | None = None, confirm: bool = False, allow_warn: bool = False, approval_note: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    require_env_gate("CMS_MODEL_OPS_ENABLE_PROMOTION_WRITES", "promotion writes", env)
    if not confirm:
        raise ModelOpsError(400, "confirm=true is required for promotion")
    validate_model_kind(model_kind)
    validate_run_id(run_id)
    ops_paths = paths_for(model_kind, env)
    candidate_root = ops_paths.candidates_root / run_id
    if model_kind == "pmax" and truthy(_env(env).get("CMS_MODEL_OPS_USE_PMAX_PROMOTION")):
        from cms.modeling.pmax import promotion as pmax_promotion

        smoke_runner = None
        if not truthy(_env(env).get("CMS_MODEL_OPS_ENABLE_PMAX_SMOKE")):
            smoke_runner = lambda deployed_root: {"result": "pass", "skipped_by_env": True, "deployed_root": str(deployed_root)}
        try:
            return pmax_promotion.promote_candidate(
                candidate_root,
                ops_paths.deployed_root,
                approval_note=approval_note,
                allow_warn=allow_warn,
                archives_root=ops_paths.archives_root,
                smoke_runner=smoke_runner,
            )
        except Exception as exc:
            raise ModelOpsError(500, f"P-Max promotion failed: {type(exc).__name__}: {exc}") from exc

    if model_kind == "anomaly":
        if horizon not in (1, 3):
            raise ModelOpsError(400, "anomaly horizon must be 1 or 3")
        from cms.modeling.anomaly import promotion as anomaly_promotion

        smoke_runner = None
        if not truthy(_env(env).get("CMS_MODEL_OPS_ENABLE_ANOMALY_SMOKE")):
            smoke_runner = lambda deployed_root, horizon: {
                "result": "pass",
                "skipped_by_env": True,
                "deployed_root": str(deployed_root),
                "horizon": horizon,
            }
        try:
            return anomaly_promotion.promote_candidate(
                candidate_root,
                ops_paths.deployed_root,
                horizon=horizon,
                approval_note=approval_note,
                allow_warn=allow_warn,
                archives_root=ops_paths.archives_root,
                smoke_runner=smoke_runner,
            )
        except Exception as exc:
            raise ModelOpsError(500, f"Anomaly promotion failed: {type(exc).__name__}: {exc}") from exc

    status = candidate_status(model_kind, run_id, horizon=horizon, env=env)
    if not status["layout_ok"]:
        raise ModelOpsError(400, "candidate artifact layout is incomplete")
    marker = _assert_model_ops_validation_current(candidate_root, allow_warn=allow_warn)
    promoted_at = datetime.now(timezone.utc)
    stamp = promoted_at.strftime("%Y%m%dT%H%M%SZ")
    deployed_root = ops_paths.deployed_root
    backup_root = ops_paths.archives_root / f"{stamp}_{run_id}_previous"
    staging_root = deployed_root.with_name(f".{deployed_root.name}.staging")
    if staging_root.exists():
        shutil.rmtree(staging_root)
    if backup_root.exists():
        raise ModelOpsError(409, f"backup path already exists: {backup_root}")
    ops_paths.archives_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(candidate_root, staging_root)
    result = {
        "status": "promoted",
        "model_kind": model_kind,
        "run_id": run_id,
        "horizon": horizon,
        "promoted_at": promoted_at.isoformat(),
        "candidate_root": str(candidate_root),
        "deployed_root": str(deployed_root),
        "backup_root": None,
        "approval_note": approval_note,
        "validation": marker,
    }
    try:
        if deployed_root.exists():
            shutil.copytree(deployed_root, backup_root)
            result["backup_root"] = str(backup_root)
            shutil.rmtree(deployed_root)
        staging_root.rename(deployed_root)
        (deployed_root / "promotion.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        shutil.rmtree(staging_root, ignore_errors=True)
        if not deployed_root.exists() and backup_root.exists():
            shutil.copytree(backup_root, deployed_root)
        raise ModelOpsError(500, f"promotion failed: {type(exc).__name__}: {exc}") from exc
    return result


def rollback_deployment(model_kind: str, archive_root: str | Path, *, horizon: int | None = None, confirm: bool = False, approval_note: str | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    require_env_gate("CMS_MODEL_OPS_ENABLE_PROMOTION_WRITES", "rollback writes", env)
    if not confirm:
        raise ModelOpsError(400, "confirm=true is required for rollback")
    validate_model_kind(model_kind)
    ops_paths = paths_for(model_kind, env)
    archive = Path(archive_root).resolve()
    if not archive.is_dir():
        raise ModelOpsError(404, f"rollback archive not found: {archive}")
    if model_kind == "pmax" and truthy(_env(env).get("CMS_MODEL_OPS_USE_PMAX_PROMOTION")):
        from cms.modeling.pmax import promotion as pmax_promotion

        try:
            return pmax_promotion.rollback_deployment(archive, ops_paths.deployed_root, approval_note=approval_note, archives_root=ops_paths.archives_root)
        except Exception as exc:
            raise ModelOpsError(500, f"P-Max rollback failed: {type(exc).__name__}: {exc}") from exc
    if model_kind == "anomaly":
        if horizon not in (1, 3):
            raise ModelOpsError(400, "anomaly horizon must be 1 or 3")
        from cms.modeling.anomaly import promotion as anomaly_promotion

        try:
            return anomaly_promotion.rollback_deployment(
                archive,
                ops_paths.deployed_root,
                horizon=horizon,
                approval_note=approval_note,
            )
        except Exception as exc:
            raise ModelOpsError(500, f"Anomaly rollback failed: {type(exc).__name__}: {exc}") from exc
    rolled_back_at = datetime.now(timezone.utc)
    stamp = rolled_back_at.strftime("%Y%m%dT%H%M%SZ")
    deployed_root = ops_paths.deployed_root
    backup_root = ops_paths.archives_root / f"{stamp}_rollback_replaced"
    staging_root = deployed_root.with_name(f".{deployed_root.name}.rollback_staging")
    if staging_root.exists():
        shutil.rmtree(staging_root)
    ops_paths.archives_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(archive, staging_root)
    result = {
        "status": "rolled_back",
        "model_kind": model_kind,
        "rolled_back_at": rolled_back_at.isoformat(),
        "source_archive": str(archive),
        "deployed_root": str(deployed_root),
        "replaced_deployment_backup": None,
        "approval_note": approval_note,
    }
    try:
        if deployed_root.exists():
            shutil.copytree(deployed_root, backup_root)
            result["replaced_deployment_backup"] = str(backup_root)
            shutil.rmtree(deployed_root)
        staging_root.rename(deployed_root)
        (deployed_root / "rollback.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception as exc:
        shutil.rmtree(staging_root, ignore_errors=True)
        if not deployed_root.exists() and backup_root.exists():
            shutil.copytree(backup_root, deployed_root)
        raise ModelOpsError(500, f"rollback failed: {type(exc).__name__}: {exc}") from exc
    return result
