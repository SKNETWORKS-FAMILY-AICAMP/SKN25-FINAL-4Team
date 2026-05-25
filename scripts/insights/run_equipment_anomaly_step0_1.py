"""Build Step 0-1 evidence tables for EMS/FEMS equipment-anomaly viability.

This script is read-only against the EMS PostgreSQL schema. It writes compact CSV
artifacts under outputs/tables/equipment_anomaly_validation/.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg

ROOT = Path(__file__).resolve().parents[2]
EMS_ROOT = Path("/home/viowlet/Projects/EMS")
OUT = ROOT / "outputs" / "tables" / "equipment_anomaly_validation"
PLAN_NAME = "2026-05-22_200752-equipment_anomaly_validation.md"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect() -> psycopg.Connection:
    load_env(EMS_ROOT / ".env")
    load_env(ROOT / ".env")
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def frame_from_query(conn: psycopg.Connection, sql: str, params: dict | None = None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        rows = cur.fetchall()
        cols = [desc.name for desc in cur.description]
    return pd.DataFrame(rows, columns=cols)


def write_plan_copy() -> Path | None:
    src = EMS_ROOT / ".hermes" / "plans" / PLAN_NAME
    dst = ROOT / ".hermes" / "plans" / PLAN_NAME
    if not src.exists():
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


def group_meters(meter_groups: pd.DataFrame, group: str) -> str:
    vals = meter_groups.loc[meter_groups["equipment_group"] == group, "meters"].dropna().astype(str).tolist()
    return " ; ".join(vals)


def reduced_has(
    reduced_cov: pd.DataFrame,
    *,
    category: str | None = None,
    subcategory: str | None = None,
    measurement: str | None = None,
    resolution: str | None = None,
) -> bool:
    frame = reduced_cov.copy()
    if resolution is not None:
        frame = frame[frame["resolution"] == resolution]
    if category is not None:
        frame = frame[frame["category"] == category]
    if subcategory is not None:
        frame = frame[frame["subcategory"] == subcategory]
    if measurement is not None:
        frame = frame[frame["measurement"] == measurement]
    return len(frame) > 0


def reduced_basis(
    reduced_cov: pd.DataFrame,
    *,
    category: str | None = None,
    subcategory: str | None = None,
    measurement: str | None = None,
    resolution: str = "15min",
) -> str:
    frame = reduced_cov[reduced_cov["resolution"] == resolution]
    if category is not None:
        frame = frame[frame["category"] == category]
    if subcategory is not None:
        frame = frame[frame["subcategory"] == subcategory]
    if measurement is not None:
        frame = frame[frame["measurement"] == measurement]
    if len(frame) == 0:
        return ""
    return "; ".join(
        f"{row.category}/{row.subcategory}/{row.measurement}: {row.rows} rows {row.min_ts}~{row.max_ts}"
        for row in frame.itertuples()
    )


def source_file_basis(signals: pd.DataFrame, group: str) -> str:
    frame = signals[(signals["equipment_group"] == group) & (signals["measurement"] == "P")]
    if frame.empty:
        return "no P source-file metadata"
    parts = []
    for row in frame.itertuples():
        parts.append(f"{row.meter_urn}:{row.resolution_code} files={row.source_files} inserted={row.inserted_rows}")
    return "; ".join(parts[:40])


def preliminary_grade(module: str, core_signals: str) -> str:
    if module in {"Cooling efficiency", "CHP operation", "PV performance"} and core_signals == "yes":
        return "A~B"
    if module == "Server cooling mismatch" and core_signals == "partial":
        return "B~C"
    if module == "Ventilation / baseload" and core_signals == "partial":
        return "C"
    if core_signals in {"yes", "partial"}:
        return "C~D"
    return "X"


def build_module_matrix(meter_groups: pd.DataFrame, signals: pd.DataFrame, reduced_cov: pd.DataFrame) -> pd.DataFrame:
    meter_groups["equipment_group"] = meter_groups["equipment_group"].astype(str)

    def has_group(group: str) -> bool:
        return bool((meter_groups["equipment_group"] == group).any())

    modules = [
        {
            "module": "Cooling efficiency",
            "priority": 1,
            "entity_scope": "equipment_group",
            "verified_db_basis": "central_cooling electricity P + cooling_thermal thermal P + reduced cooling total/cool_elec + weather Ta",
            "core_db_signals_present": "yes"
            if has_group("central_cooling")
            and has_group("cooling_thermal")
            and reduced_has(reduced_cov, category="cooling", measurement="P")
            and reduced_has(reduced_cov, category="weather", subcategory="weather", measurement="Ta")
            else "partial",
            "meter_groups": f"central_cooling: {group_meters(meter_groups, 'central_cooling')} | cooling_thermal: {group_meters(meter_groups, 'cooling_thermal')}",
            "source_file_signal_basis": f"{source_file_basis(signals, 'central_cooling')} | {source_file_basis(signals, 'cooling_thermal')}",
            "reduced_basis": f"{reduced_basis(reduced_cov, category='cooling', measurement='P')} | {reduced_basis(reduced_cov, category='weather', subcategory='weather', measurement='Ta')}",
            "main_missing_context": "개별 냉각기 모델명, 정격 COP, 운전 command, 밸브/펌프 상태",
            "first_test": "cooling electric P ~ cooling thermal P + Ta + hour + month",
        },
        {
            "module": "CHP operation",
            "priority": 1,
            "entity_scope": "equipment_group",
            "verified_db_basis": "chp electricity P + chp_heat_generation thermal P + heating total/chp_heat/chp_elec + weather Ta",
            "core_db_signals_present": "yes"
            if has_group("chp")
            and has_group("chp_heat_generation")
            and reduced_has(reduced_cov, category="heating", subcategory="chp_heat", measurement="P")
            and reduced_has(reduced_cov, category="electricity", subcategory="chp", measurement="P")
            else "partial",
            "meter_groups": f"chp: {group_meters(meter_groups, 'chp')} | chp_heat_generation: {group_meters(meter_groups, 'chp_heat_generation')} | heat_generation: {group_meters(meter_groups, 'heat_generation')}",
            "source_file_signal_basis": f"{source_file_basis(signals, 'chp')} | {source_file_basis(signals, 'chp_heat_generation')} | {source_file_basis(signals, 'heat_generation')}",
            "reduced_basis": f"{reduced_basis(reduced_cov, category='electricity', subcategory='chp', measurement='P')} | {reduced_basis(reduced_cov, category='heating', subcategory='chp_heat', measurement='P')} | {reduced_basis(reduced_cov, category='heating', subcategory='total', measurement='P')}",
            "main_missing_context": "CHP on/off command, alarm, maintenance log, heat-demand threshold 실제값",
            "first_test": "CHP electric P ~ CHP heat P + heating total P + Ta + month + regime",
        },
        {
            "module": "PV performance",
            "priority": 1,
            "entity_scope": "equipment_group / meter",
            "verified_db_basis": "pv electricity P + reduced electricity/pv + weather Igm",
            "core_db_signals_present": "yes"
            if has_group("pv")
            and reduced_has(reduced_cov, category="electricity", subcategory="pv", measurement="P")
            and reduced_has(reduced_cov, category="weather", subcategory="weather", measurement="Igm")
            else "partial",
            "meter_groups": f"pv: {group_meters(meter_groups, 'pv')}",
            "source_file_signal_basis": source_file_basis(signals, "pv"),
            "reduced_basis": f"{reduced_basis(reduced_cov, category='electricity', subcategory='pv', measurement='P')} | {reduced_basis(reduced_cov, category='weather', subcategory='weather', measurement='Igm')}",
            "main_missing_context": "인버터별 용량, string 구성, 패널별 연결, 음영/청소/정비 이력",
            "first_test": "PV P ~ Igm + local hour + month + installation regime + meter/group",
        },
        {
            "module": "Server cooling mismatch",
            "priority": 2,
            "entity_scope": "equipment_group",
            "verified_db_basis": "server_power electricity P + local_cooling electricity P + server_thermal thermal P + weather Ta",
            "core_db_signals_present": "partial" if has_group("server_power") and has_group("local_cooling") else "no",
            "meter_groups": f"server_power: {group_meters(meter_groups, 'server_power')} | local_cooling: {group_meters(meter_groups, 'local_cooling')} | server_thermal: {group_meters(meter_groups, 'server_thermal')}",
            "source_file_signal_basis": f"{source_file_basis(signals, 'server_power')} | {source_file_basis(signals, 'local_cooling')} | {source_file_basis(signals, 'server_thermal')}",
            "reduced_basis": reduced_basis(reduced_cov, category="weather", subcategory="weather", measurement="Ta"),
            "main_missing_context": "server IT load boundary, local cooling control state, setpoint, occupancy/operation context",
            "first_test": "local cooling P ~ server power P + Ta + hour + weekday",
        },
        {
            "module": "Ventilation / baseload",
            "priority": 2,
            "entity_scope": "equipment_group / operation pattern",
            "verified_db_basis": "ventilation electricity P + office/workshop distribution + weather Ta + calendar",
            "core_db_signals_present": "partial" if has_group("ventilation") else "no",
            "meter_groups": f"ventilation: {group_meters(meter_groups, 'ventilation')} | office_distribution: {group_meters(meter_groups, 'office_distribution')} | workshop_test: {group_meters(meter_groups, 'workshop_test')}",
            "source_file_signal_basis": f"{source_file_basis(signals, 'ventilation')} | {source_file_basis(signals, 'office_distribution')} | {source_file_basis(signals, 'workshop_test')}",
            "reduced_basis": reduced_basis(reduced_cov, category="weather", subcategory="weather", measurement="Ta"),
            "main_missing_context": "운전 스케줄, occupancy, BMS command, fan/AHU 개별 상태",
            "first_test": "ventilation P ~ Ta + hour + weekday + month; night/weekend baseload clustering",
        },
        {
            "module": "Heating / boiler",
            "priority": 3,
            "entity_scope": "system",
            "verified_db_basis": "heat_generation thermal P + chp_heat_generation thermal P + heating total + weather Ta",
            "core_db_signals_present": "partial"
            if has_group("heat_generation") and reduced_has(reduced_cov, category="heating", subcategory="total", measurement="P")
            else "no",
            "meter_groups": f"heat_generation: {group_meters(meter_groups, 'heat_generation')} | chp_heat_generation: {group_meters(meter_groups, 'chp_heat_generation')}",
            "source_file_signal_basis": f"{source_file_basis(signals, 'heat_generation')} | {source_file_basis(signals, 'chp_heat_generation')}",
            "reduced_basis": f"{reduced_basis(reduced_cov, category='heating', subcategory='total', measurement='P')} | {reduced_basis(reduced_cov, category='heating', subcategory='chp_heat', measurement='P')}",
            "main_missing_context": "개별 boiler meter, boiler command/alarm, return-temperature 실제 series 확인 필요",
            "first_test": "heating total P ~ Ta + month + CHP heat P + modernization regime",
        },
        {
            "module": "Transformer / grid boundary",
            "priority": 3,
            "entity_scope": "site boundary / meter group",
            "verified_db_basis": "grid_transformer electricity P + electricity total + pv/chp context",
            "core_db_signals_present": "partial"
            if has_group("grid_transformer") and reduced_has(reduced_cov, category="electricity", subcategory="total", measurement="P")
            else "no",
            "meter_groups": f"grid_transformer: {group_meters(meter_groups, 'grid_transformer')}",
            "source_file_signal_basis": source_file_basis(signals, "grid_transformer"),
            "reduced_basis": f"{reduced_basis(reduced_cov, category='electricity', subcategory='total', measurement='P')} | {reduced_basis(reduced_cov, category='electricity', subcategory='pv', measurement='P')} | {reduced_basis(reduced_cov, category='electricity', subcategory='chp', measurement='P')}",
            "main_missing_context": "계약전력/전기요금 청구 데이터, 단선도 상세, transformer replacement gap 처리",
            "first_test": "signed grid P consistency + PV/CHP context + replacement-gap flags",
        },
    ]
    frame = pd.DataFrame(modules)
    frame["preliminary_grade"] = [preliminary_grade(row.module, row.core_db_signals_present) for row in frame.itertuples()]
    frame["interpretation_boundary"] = "고장 확정 제외; 성능 이상 후보·운영 점검 후보로 제한"
    return frame


def write_brief(
    relations: pd.DataFrame,
    table_coverage: pd.DataFrame,
    module_df: pd.DataFrame,
    plan_path: Path | None,
) -> None:
    lines = [
        "# Step 0-1 실증확인 기준선 요약\n",
        f"- 생성 시각(UTC): {datetime.now(timezone.utc).isoformat()}",
        f"- 작업 root: `{ROOT}`",
        f"- DB: `{os.environ.get('DB_HOST')}:{os.environ.get('DB_PORT')}/{os.environ.get('DB_NAME')}` user `{os.environ.get('DB_USER')}`",
        "- DB password는 출력하지 않음",
        "\n## 확인된 relation\n",
    ]
    for row in relations.itertuples():
        lines.append(f"- `{row.table_schema}.{row.table_name}`: {row.table_type}")
    lines.append("\n## 주요 table coverage\n")
    for row in table_coverage.itertuples():
        lines.append(
            f"- `{row.relation}`: rows={row.rows}, distinct_ts={row.distinct_ts}, range={row.min_ts} ~ {row.max_ts}"
        )
    lines.append("\n## 모듈별 1차 판정\n")
    for row in module_df.itertuples():
        lines.append(
            f"- {row.module}: {row.preliminary_grade}, core_signals={row.core_db_signals_present}, first_test=`{row.first_test}`"
        )
    lines.append("\n## 생성 파일\n")
    for path in sorted(OUT.glob("*.csv")):
        lines.append(f"- `{path}`")
    lines.append(f"- `{OUT / 'STEP0_STEP1_BRIEF.md'}`")
    if plan_path is not None:
        lines.append(f"- plan copy: `{plan_path}`")
    (OUT / "STEP0_STEP1_BRIEF.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    plan_path = write_plan_copy()
    checked_at = datetime.now(timezone.utc).isoformat()
    conn = connect()
    conn.execute("set default_transaction_read_only = on")
    conn.execute("set statement_timeout = '300s'")

    env_df = pd.DataFrame(
        [
            {"item": "project_root", "value": str(ROOT), "status": "confirmed"},
            {"item": "db_host", "value": os.environ.get("DB_HOST", ""), "status": "confirmed_nonsecret"},
            {"item": "db_port", "value": os.environ.get("DB_PORT", ""), "status": "confirmed_nonsecret"},
            {"item": "db_name", "value": os.environ.get("DB_NAME", ""), "status": "confirmed_nonsecret"},
            {"item": "db_user", "value": os.environ.get("DB_USER", ""), "status": "confirmed_nonsecret"},
            {"item": "db_password", "value": "<present>" if os.environ.get("DB_PASSWORD") else "<missing>", "status": "secret_not_printed"},
            {"item": "checked_at_utc", "value": checked_at, "status": "confirmed"},
            {"item": "source_env", "value": str(EMS_ROOT / ".env"), "status": "confirmed"},
        ]
    )
    env_df.to_csv(OUT / "00_environment_check.csv", index=False)

    relations = frame_from_query(
        conn,
        """
        select table_schema, table_name, table_type
        from information_schema.tables
        where table_schema='ems' and table_name in (
         'cr_measurement_15min','cr_measurement_1h','reduced_measurement_15min','reduced_measurement_1h',
         'meter_definition','meter_redundancy','full_source_file','full_measurement_definition'
        )
        order by table_name
        """,
    )
    relations.to_csv(OUT / "00_relation_inventory.csv", index=False)

    table_rows = []
    for relation, resolution_seconds in [
        ("ems.cr_measurement_15min", 900),
        ("ems.cr_measurement_1h", 3600),
        ("ems.reduced_measurement_15min", 900),
        ("ems.reduced_measurement_1h", 3600),
    ]:
        row = frame_from_query(
            conn,
            f"select count(*) as rows, count(distinct ts) as distinct_ts, min(ts) as min_ts, max(ts) as max_ts from {relation}",
        ).iloc[0].to_dict()
        row["relation"] = relation
        row["resolution_seconds"] = resolution_seconds
        table_rows.append(row)
    table_coverage = pd.DataFrame(table_rows)[["relation", "rows", "distinct_ts", "min_ts", "max_ts", "resolution_seconds"]]
    table_coverage.to_csv(OUT / "00_table_coverage.csv", index=False)

    load_balance = frame_from_query(
        conn,
        """
        select processing_level, resolution_code, status,
               count(*) as files,
               sum(csv_rows) as csv_rows,
               sum(inserted_rows) as inserted_rows,
               sum(conflict_rows) as conflict_rows,
               sum(duplicate_key_rows) as duplicate_key_rows,
               sum(null_value_rows) as null_value_rows,
               sum(invalid_value_rows) as invalid_value_rows,
               sum(invalid_ts_rows) as invalid_ts_rows
        from ems.full_source_file
        group by processing_level, resolution_code, status
        order by processing_level, resolution_code, status
        """,
    )
    load_balance.to_csv(OUT / "00_source_load_balance.csv", index=False)

    meter_groups = frame_from_query(
        conn,
        """
        select equipment_group, meter_domain, meter_role, building_code,
               count(*) as meter_count,
               string_agg(meter_urn, ', ' order by meter_urn) as meters,
               string_agg(coalesce(equipment_name,''), ' | ' order by meter_urn) as equipment_names
        from ems.meter_definition
        group by equipment_group, meter_domain, meter_role, building_code
        order by equipment_group, meter_domain, building_code, meter_role
        """,
    )
    meter_groups.to_csv(OUT / "01_meter_group_inventory.csv", index=False)

    signals = frame_from_query(
        conn,
        """
        select md.equipment_group, md.meter_domain, md.meter_role, md.building_code,
               md.meter_urn, md.equipment_name, md.sign_convention,
               f.processing_level, f.resolution_code, f.measurement,
               count(f.source_file) as source_files,
               sum(f.csv_rows) as csv_rows,
               sum(f.inserted_rows) as inserted_rows,
               sum(f.null_value_rows) as null_value_rows,
               sum(f.invalid_value_rows) as invalid_value_rows,
               sum(f.invalid_ts_rows) as invalid_ts_rows
        from ems.meter_definition md
        left join ems.full_source_file f
          on f.meter_urn = md.meter_urn
         and f.measurement = 'P'
         and f.processing_level = 'corrected_resampled'
         and f.resolution_code in ('15min','1h')
        group by md.equipment_group, md.meter_domain, md.meter_role, md.building_code,
                 md.meter_urn, md.equipment_name, md.sign_convention,
                 f.processing_level, f.resolution_code, f.measurement
        order by md.equipment_group, md.meter_urn, f.resolution_code
        """,
    )
    signals.to_csv(OUT / "01_meter_signal_inventory.csv", index=False)

    reduced_cov = frame_from_query(
        conn,
        """
        select '15min' as resolution, category, subcategory, measurement, count(*) as rows, min(ts) as min_ts, max(ts) as max_ts
        from ems.reduced_measurement_15min
        group by category, subcategory, measurement
        union all
        select '1h' as resolution, category, subcategory, measurement, count(*) as rows, min(ts) as min_ts, max(ts) as max_ts
        from ems.reduced_measurement_1h
        group by category, subcategory, measurement
        order by resolution, category, subcategory, measurement
        """,
    )
    reduced_cov.to_csv(OUT / "01_reduced_signal_coverage.csv", index=False)

    module_df = build_module_matrix(meter_groups, signals, reduced_cov)
    module_df.to_csv(OUT / "01_module_availability_matrix.csv", index=False)
    write_brief(relations, table_coverage, module_df, plan_path)
    conn.close()

    print(
        json.dumps(
            {
                "out_dir": str(OUT),
                "files": [path.name for path in sorted(OUT.glob("*"))],
                "module_grades": module_df[["module", "core_db_signals_present", "preliminary_grade"]].to_dict(orient="records"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
