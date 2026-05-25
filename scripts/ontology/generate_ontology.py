#!/usr/bin/env python3
"""Generate EMS RDF/Turtle ontology artifacts.

Default source:
- PostgreSQL/TimescaleDB approved metadata tables under the `ems` schema.

Optional legacy source:
- docs/_archive/notes/meter_graph/계량기_*.md frontmatter
- docs/_archive/notes/meter_graph/그룹_*.md frontmatter
- docs/specs/계량기_메타데이터.md redundancy table

Output:
- docs/ontology/ems.ttl
- docs/ontology/ems_protege.owl
"""

from __future__ import annotations

from pathlib import Path
import argparse
import re
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD

ROOT = Path(__file__).resolve().parents[2]
GRAPH_DIR = ROOT / "docs/_archive/notes/meter_graph"
SPEC_PATH = ROOT / "docs/specs/계량기_메타데이터.md"
OUTPUT_PATH = ROOT / "docs/ontology/ems.ttl"
PROTEGE_OUTPUT_PATH = ROOT / "docs/ontology/ems_protege.owl"

ONTOLOGY_IRI = URIRef("https://nousresearch.local/ems/ontology")
EMS = Namespace("https://nousresearch.local/ems/ontology#")
RES = Namespace("https://nousresearch.local/ems/resource/")

ROLE_SIGN_CONVENTIONS = {
    "consumption": "positive_consumption_negative_quality_candidate",
    "production": "negative_production_positive_noise_or_reverse_flow",
    "thermal_flow": "direction_depends_on_equipment_context",
    "weather": "no_sign_convention",
}

GROUP_ANOMALY_PRIORITIES = {
    "central_cooling": 1,
    "chp": 1,
    "grid_transformer": 1,
    "pv": 2,
    "local_cooling": 2,
    "server_power": 2,
    "heat_generation": 2,
    "chp_heat_generation": 2,
    "server_thermal": 2,
    "emission_lab": 3,
    "ventilation": 3,
    "workshop_test": 3,
    "office_distribution": 3,
    "design_studio_distribution": 3,
    "cooling_thermal": 3,
    "hvac_thermal": 3,
    "weather_station": 4,
}

GROUP_PRIMARY_VIEWS = {
    "central_cooling": "central_cooling_graph",
    "chp": "building_energy_flow",
    "chp_heat_generation": "building_energy_flow",
    "cooling_thermal": "building_energy_flow",
    "design_studio_distribution": "meter_inventory_graph",
    "emission_lab": "meter_inventory_graph",
    "grid_transformer": "building_energy_flow",
    "heat_generation": "building_energy_flow",
    "hvac_thermal": "building_energy_flow",
    "local_cooling": "central_cooling_graph",
    "office_distribution": "meter_inventory_graph",
    "pv": "building_energy_flow",
    "server_power": "meter_inventory_graph",
    "server_thermal": "building_energy_flow",
    "ventilation": "meter_inventory_graph",
    "weather_station": "building_energy_flow",
    "workshop_test": "meter_inventory_graph",
}

REDUNDANCY_POLICIES = {
    "exclude_redundant_endpoint": "Aggregate feature에서 redundant endpoint를 제외",
    "pair_comparison": "Primary와 redundant endpoint를 비교 feature로 분리",
}

FEATURE_RULES = {
    "energy_aggregate_excludes_weather": "Energy aggregate에는 weather meter를 포함하지 않음",
    "aggregate_excludes_redundant": "Aggregate feature에서는 redundant endpoint를 기본 제외",
    "live_replay_no_future": "Live replay feature는 현재 tick 이전 데이터만 사용",
}

FOCUSED_VIEW_BY_GROUP = {
    "central_cooling": RES.view_focus_central_cooling,
    "server_power": RES.view_focus_server_power,
    "emission_lab": RES.view_focus_emission_lab,
}


def parse_frontmatter(path: Path) -> dict[str, object]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict[str, object] = {}
    key: str | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*:", line):
            k, v = line.split(":", 1)
            key = k.strip()
            v = v.strip()
            data[key] = [] if not v else v.strip('"')
        elif line.strip().startswith("- ") and key:
            item = line.strip()[2:].strip().strip('"')
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(item)  # type: ignore[union-attr]
    return data


def slug(value: str) -> str:
    value = value.replace(".", "_").replace("-", "_").replace("/", "_").replace(" ", "_")
    value = re.sub(r"[^A-Za-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value:
        return "unknown"
    if value[0].isdigit():
        value = "_" + value
    return value


def load_meter_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(GRAPH_DIR.glob("계량기_*.md")):
        fm = parse_frontmatter(path)
        if fm.get("type") != "meter":
            continue
        fm["note_file"] = str(path.relative_to(ROOT))
        records.append(fm)
    return records


def load_group_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(GRAPH_DIR.glob("그룹_*.md")):
        fm = parse_frontmatter(path)
        if fm.get("type") != "equipment_group":
            continue
        fm["note_file"] = str(path.relative_to(ROOT))
        records.append(fm)
    return records


def build_group_records_from_meters(meter_records: list[dict[str, object]]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for code in sorted({str(rec["equipment_group"]) for rec in meter_records}):
        count = sum(1 for rec in meter_records if str(rec["equipment_group"]) == code)
        records.append({
            "type": "equipment_group",
            "equipment_group": code,
            "meter_count": str(count),
            "primary_view": GROUP_PRIMARY_VIEWS[code],
            "note_file": f"db:ems.meter_definition/group/{code}",
        })
    return records


def load_redundancy_records() -> list[dict[str, str]]:
    text = SPEC_PATH.read_text(encoding="utf-8")
    records: list[dict[str, str]] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## 6. Redundancy mapping"):
            in_section = True
            continue
        if in_section and line.startswith("---"):
            break
        if in_section and line.startswith("| `"):
            parts = [p.strip() for p in line.strip("|").split("|")]
            if len(parts) >= 4:
                records.append({
                    "primary_meter_urn": parts[0].strip("`"),
                    "redundant_meter_urn": parts[1].strip("`"),
                    "equipment_group": parts[2].strip("`"),
                    "equipment_name": parts[3],
                })
    return records


def add_class(graph: Graph, local_name: str, label: str) -> None:
    graph.add((EMS[local_name], RDF.type, OWL.Class))
    graph.add((EMS[local_name], RDFS.label, Literal(label, lang="ko")))


def add_object_property(graph: Graph, local_name: str, domain, range_, label: str) -> None:
    graph.add((EMS[local_name], RDF.type, OWL.ObjectProperty))
    graph.add((EMS[local_name], RDFS.domain, domain))
    graph.add((EMS[local_name], RDFS.range, range_))
    graph.add((EMS[local_name], RDFS.label, Literal(label, lang="ko")))


def add_data_property(graph: Graph, local_name: str, domain, range_, label: str) -> None:
    graph.add((EMS[local_name], RDF.type, OWL.DatatypeProperty))
    graph.add((EMS[local_name], RDFS.domain, domain))
    graph.add((EMS[local_name], RDFS.range, range_))
    graph.add((EMS[local_name], RDFS.label, Literal(label, lang="ko")))


def build_graph(source: str = "db") -> Graph:
    hardware_records: list[dict[str, str]] = []
    if source == "db":
        from load_metadata_from_db import fetch_db_metadata

        meter_records, redundancy_records, hardware_records = fetch_db_metadata()
        group_records = build_group_records_from_meters(meter_records)
    else:
        meter_records = load_meter_records()
        redundancy_records = load_redundancy_records()
        group_records = load_group_records()
    redundancy_group_codes = {rec["equipment_group"] for rec in redundancy_records}

    expected = {"meters": 81, "groups": 17, "redundancy_pairs": 12}
    actual = {"meters": len(meter_records), "groups": len(group_records), "redundancy_pairs": len(redundancy_records)}
    if source == "db":
        expected["hardware_assignments"] = 81
        actual["hardware_assignments"] = len(hardware_records)
    if actual != expected:
        raise SystemExit(f"unexpected source counts: expected={expected}, actual={actual}")

    graph = Graph()
    graph.bind("rdf", RDF)
    graph.bind("ems", EMS)
    graph.bind("emsres", RES)
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    graph.bind("xsd", XSD)

    graph.add((ONTOLOGY_IRI, RDF.type, OWL.Ontology))
    graph.add((ONTOLOGY_IRI, RDFS.label, Literal("EMS ontology", lang="en")))
    graph.add((ONTOLOGY_IRI, RDFS.label, Literal("EMS 온톨로지", lang="ko")))
    graph.add((ONTOLOGY_IRI, RDFS.comment, Literal("EMS 계량기 metadata, 설비 group, 건물, 역할, redundancy 관계를 정의한 ontology artifact", lang="ko")))

    for name, label in {
        "Meter": "EMS 계량기",
        "ElectricityMeter": "전기 계량기",
        "ThermalMeter": "열 계량기",
        "WeatherMeter": "기상 계량기",
        "EquipmentGroup": "설비 또는 계통 group",
        "Building": "건물 또는 구역",
        "MeterRole": "계량기 분석 역할",
        "RedundancyPair": "중복 계량 pair",
        "VisualizationView": "Canvas, Mermaid, Excalidraw 보기",
        "MetadataDocument": "기준 metadata 문서",
        "Feature": "향후 feature 정의",
        "AnomalyEvent": "향후 anomaly event 정의",
        "MeterDomain": "계측 domain vocabulary",
        "SignConvention": "부호 해석 vocabulary",
        "AnomalyPriorityLevel": "이상탐지 우선순위 vocabulary",
        "RedundancyPolicy": "중복 계량 처리 정책",
        "FeatureRule": "feature 생성 규칙",
        "HardwareModel": "계량기 하드웨어 모델",
    }.items():
        add_class(graph, name, label)

    for sub, sup in [("ElectricityMeter", "Meter"), ("ThermalMeter", "Meter"), ("WeatherMeter", "Meter")]:
        graph.add((EMS[sub], RDFS.subClassOf, EMS[sup]))
    for left, right in [("ElectricityMeter", "ThermalMeter"), ("ElectricityMeter", "WeatherMeter"), ("ThermalMeter", "WeatherMeter")]:
        graph.add((EMS[left], OWL.disjointWith, EMS[right]))

    for name, domain, range_, label in [
        ("belongsToGroup", EMS.Meter, EMS.EquipmentGroup, "meter가 equipment group에 속함"),
        ("locatedInBuilding", EMS.Meter, EMS.Building, "meter가 building 또는 zone에 속함"),
        ("hasRole", EMS.Meter, EMS.MeterRole, "meter의 분석 역할"),
        ("redundantWith", EMS.Meter, EMS.Meter, "중복 계량 관계"),
        ("hasPrimaryMeter", EMS.RedundancyPair, EMS.Meter, "redundancy pair의 primary meter"),
        ("hasRedundantMeter", EMS.RedundancyPair, EMS.Meter, "redundancy pair의 redundant meter"),
        ("hasGroup", EMS.RedundancyPair, EMS.EquipmentGroup, "redundancy pair가 속한 group"),
        ("visualizedBy", OWL.Thing, EMS.VisualizationView, "entity를 확인할 수 있는 view"),
        ("definedBy", OWL.Thing, EMS.MetadataDocument, "entity 기준 문서"),
        ("hasDomain", EMS.Meter, EMS.MeterDomain, "meter의 계측 domain vocabulary"),
        ("hasSignConvention", OWL.Thing, EMS.SignConvention, "부호 해석 vocabulary"),
        ("hasAnomalyPriorityLevel", OWL.Thing, EMS.AnomalyPriorityLevel, "이상탐지 우선순위 vocabulary"),
        ("usesRedundancyPolicy", EMS.FeatureRule, EMS.RedundancyPolicy, "feature rule이 사용하는 redundancy policy"),
        ("usesMeterSetRule", EMS.Feature, EMS.FeatureRule, "feature가 사용하는 meter set rule"),
        ("hasHardwareModel", EMS.Meter, EMS.HardwareModel, "meter의 하드웨어 모델"),
    ]:
        add_object_property(graph, name, domain, range_, label)
    graph.add((EMS.redundantWith, RDF.type, OWL.SymmetricProperty))

    for name, domain, range_, label in [
        ("meterUrn", EMS.Meter, XSD.string, "DB meter identifier"),
        ("meterDomain", EMS.Meter, XSD.string, "계측 domain"),
        ("meterRoleCode", EMS.Meter, XSD.string, "계량기 role code"),
        ("equipmentGroupCode", OWL.Thing, XSD.string, "equipment group code"),
        ("equipmentName", OWL.Thing, XSD.string, "equipment name"),
        ("equipmentLayer", EMS.Meter, XSD.string, "equipment layer within a group"),
        ("buildingCode", OWL.Thing, XSD.string, "building 또는 zone code"),
        ("signConvention", OWL.Thing, XSD.string, "계량값 부호 해석 규칙"),
        ("anomalyPriority", OWL.Thing, XSD.integer, "이상탐지 검토 우선순위"),
        ("primaryView", EMS.EquipmentGroup, XSD.string, "group 기본 검토 view"),
        ("noteFile", OWL.Thing, XSD.string, "project-relative note path"),
        ("sourcePath", EMS.MetadataDocument, XSD.string, "project-relative source path"),
        ("meterCount", EMS.EquipmentGroup, XSD.integer, "group meter count"),
        ("hardwareModelCode", OWL.Thing, XSD.string, "계량기 하드웨어 모델 코드"),
        ("manufacturer", EMS.HardwareModel, XSD.string, "계량기 제조사"),
        ("modelName", EMS.HardwareModel, XSD.string, "계량기 모델명"),
        ("sourceName", OWL.Thing, XSD.string, "metadata source name"),
        ("sourceTable", OWL.Thing, XSD.string, "metadata source table"),
        ("sourceDescription", OWL.Thing, XSD.string, "metadata source description"),
    ]:
        add_data_property(graph, name, domain, range_, label)
    graph.add((EMS.meterUrn, RDF.type, OWL.FunctionalProperty))

    for name, source in {
        "meter_metadata": "docs/specs/계량기_메타데이터.md",
        "ontology_lite": "docs/_archive/notes/meter_graph/온톨로지_요약.md",
        "ontology_schema": "docs/specs/온톨로지_스키마.md",
        "nature_scientific_data_2025": "https://doi.org/10.1038/s41597-025-05186-3",
    }.items():
        uri = RES[f"doc_{name}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.MetadataDocument))
        graph.add((uri, RDFS.label, Literal(source)))
        graph.add((uri, EMS.sourcePath, Literal(source)))

    for key, source in {
        "ems_meter_system_overview": "docs/_archive/notes/meter_graph/EMS_계량기_시스템_개요.canvas",
        "ems_meter_system_all": "docs/_archive/notes/meter_graph/EMS_계량기_시스템_전체.canvas",
        "focus_central_cooling": "docs/_archive/notes/meter_graph/집중보기_중앙냉방.canvas",
        "focus_server_power": "docs/_archive/notes/meter_graph/집중보기_서버전원.canvas",
        "focus_emission_lab": "docs/_archive/notes/meter_graph/집중보기_배출실험실.canvas",
        "focus_redundancy": "docs/_archive/notes/meter_graph/집중보기_중복계량.canvas",
        "meter_inventory_graph": "docs/_archive/notes/meter_graph/계량기_목록_그래프.md",
        "redundancy_graph": "docs/_archive/notes/meter_graph/중복계량_그래프.md",
    }.items():
        uri = RES[f"view_{key}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.VisualizationView))
        graph.add((uri, RDFS.label, Literal(key)))
        graph.add((uri, EMS.sourcePath, Literal(source)))

    for domain in ["electricity", "thermal", "weather"]:
        uri = RES[f"domain_{slug(domain)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.MeterDomain))
        graph.add((uri, RDFS.label, Literal(domain)))
        graph.add((uri, EMS.meterDomain, Literal(domain)))
        graph.add((uri, EMS.definedBy, RES.doc_meter_metadata))

    for sign in sorted(set(ROLE_SIGN_CONVENTIONS.values())):
        uri = RES[f"sign_{slug(sign)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.SignConvention))
        graph.add((uri, RDFS.label, Literal(sign)))
        graph.add((uri, EMS.signConvention, Literal(sign)))
        graph.add((uri, EMS.definedBy, RES.doc_meter_metadata))

    for priority in [1, 2, 3, 4]:
        uri = RES[f"priority_{priority}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.AnomalyPriorityLevel))
        graph.add((uri, RDFS.label, Literal(f"priority {priority}")))
        graph.add((uri, EMS.anomalyPriority, Literal(priority, datatype=XSD.integer)))
        graph.add((uri, EMS.definedBy, RES.doc_meter_metadata))

    for key, label in REDUNDANCY_POLICIES.items():
        uri = RES[f"policy_{slug(key)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.RedundancyPolicy))
        graph.add((uri, RDFS.label, Literal(label, lang="ko")))
        graph.add((uri, EMS.definedBy, RES.doc_meter_metadata))

    for key, label in FEATURE_RULES.items():
        uri = RES[f"rule_{slug(key)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.FeatureRule))
        graph.add((uri, RDFS.label, Literal(label, lang="ko")))
        graph.add((uri, EMS.definedBy, RES.doc_meter_metadata))
    graph.add((RES.rule_aggregate_excludes_redundant, EMS.usesRedundancyPolicy, RES.policy_exclude_redundant_endpoint))

    for role in sorted({str(m["meter_role"]) for m in meter_records}):
        uri = RES[f"role_{slug(role)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.MeterRole))
        graph.add((uri, RDFS.label, Literal(role)))
        graph.add((uri, EMS.meterRoleCode, Literal(role)))
        graph.add((uri, EMS.signConvention, Literal(ROLE_SIGN_CONVENTIONS[role])))
        graph.add((uri, EMS.hasSignConvention, RES[f"sign_{slug(ROLE_SIGN_CONVENTIONS[role])}"]))
        graph.add((uri, EMS.definedBy, RES.doc_meter_metadata))

    for building in sorted({str(m["building_code"]) for m in meter_records}):
        uri = RES[f"building_{slug(building)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.Building))
        graph.add((uri, RDFS.label, Literal(building)))
        graph.add((uri, EMS.buildingCode, Literal(building)))
        graph.add((uri, EMS.definedBy, RES.doc_meter_metadata))

    for rec in group_records:
        code = str(rec["equipment_group"])
        uri = RES[f"group_{slug(code)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.EquipmentGroup))
        graph.add((uri, RDFS.label, Literal(code)))
        graph.add((uri, EMS.equipmentGroupCode, Literal(code)))
        meter_count = rec.get("meter_count")
        if str(meter_count).isdigit():
            graph.add((uri, EMS.meterCount, Literal(int(str(meter_count)), datatype=XSD.integer)))
        if rec.get("primary_view"):
            graph.add((uri, EMS.primaryView, Literal(str(rec["primary_view"]))))
        graph.add((uri, EMS.anomalyPriority, Literal(GROUP_ANOMALY_PRIORITIES[code], datatype=XSD.integer)))
        graph.add((uri, EMS.hasAnomalyPriorityLevel, RES[f"priority_{GROUP_ANOMALY_PRIORITIES[code]}"]))
        graph.add((uri, EMS.noteFile, Literal(str(rec["note_file"]))))
        graph.add((uri, EMS.definedBy, RES.doc_meter_metadata))
        graph.add((uri, EMS.visualizedBy, RES.view_ems_meter_system_overview))
        graph.add((uri, EMS.visualizedBy, RES.view_meter_inventory_graph))
        if code in FOCUSED_VIEW_BY_GROUP:
            graph.add((uri, EMS.visualizedBy, FOCUSED_VIEW_BY_GROUP[code]))
        if code in redundancy_group_codes:
            graph.add((uri, EMS.visualizedBy, RES.view_focus_redundancy))

    hardware_by_urn = {str(rec["meter_urn"]): rec for rec in hardware_records}
    hardware_model_records = {
        str(rec["hardware_model_code"]): rec
        for rec in hardware_records
    }
    for model_code, rec in sorted(hardware_model_records.items()):
        uri = RES[f"hardware_{slug(model_code)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.HardwareModel))
        graph.add((uri, RDFS.label, Literal(model_code)))
        graph.add((uri, EMS.hardwareModelCode, Literal(model_code)))
        graph.add((uri, EMS.manufacturer, Literal(str(rec.get("manufacturer", "")))))
        graph.add((uri, EMS.modelName, Literal(str(rec.get("model_name", "")))))
        graph.add((uri, EMS.meterDomain, Literal(str(rec.get("meter_category", "")))))
        graph.add((uri, EMS.definedBy, RES.doc_nature_scientific_data_2025))

    meter_uri_by_urn = {}
    for rec in meter_records:
        urn = str(rec["meter_urn"])
        domain = str(rec["meter_domain"])
        role = str(rec["meter_role"])
        group_code = str(rec["equipment_group"])
        building = str(rec["building_code"])
        uri = RES[f"meter_{slug(urn)}"]
        meter_uri_by_urn[urn] = uri
        meter_class = {"electricity": EMS.ElectricityMeter, "thermal": EMS.ThermalMeter, "weather": EMS.WeatherMeter}.get(domain, EMS.Meter)
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, EMS.Meter))
        graph.add((uri, RDF.type, meter_class))
        graph.add((uri, RDFS.label, Literal(urn)))
        graph.add((uri, EMS.meterUrn, Literal(urn)))
        graph.add((uri, EMS.meterDomain, Literal(domain)))
        graph.add((uri, EMS.hasDomain, RES[f"domain_{slug(domain)}"]))
        graph.add((uri, EMS.meterRoleCode, Literal(role)))
        graph.add((uri, EMS.equipmentGroupCode, Literal(group_code)))
        graph.add((uri, EMS.equipmentName, Literal(str(rec.get("equipment_name", "")))))
        if rec.get("equipment_layer"):
            graph.add((uri, EMS.equipmentLayer, Literal(str(rec["equipment_layer"]))))
        graph.add((uri, EMS.buildingCode, Literal(building)))
        graph.add((uri, EMS.signConvention, Literal(ROLE_SIGN_CONVENTIONS[role])))
        graph.add((uri, EMS.hasSignConvention, RES[f"sign_{slug(ROLE_SIGN_CONVENTIONS[role])}"]))
        graph.add((uri, EMS.anomalyPriority, Literal(GROUP_ANOMALY_PRIORITIES[group_code], datatype=XSD.integer)))
        graph.add((uri, EMS.hasAnomalyPriorityLevel, RES[f"priority_{GROUP_ANOMALY_PRIORITIES[group_code]}"]))
        graph.add((uri, EMS.noteFile, Literal(str(rec["note_file"]))))
        graph.add((uri, EMS.belongsToGroup, RES[f"group_{slug(group_code)}"]))
        graph.add((uri, EMS.locatedInBuilding, RES[f"building_{slug(building)}"]))
        graph.add((uri, EMS.hasRole, RES[f"role_{slug(role)}"]))
        graph.add((uri, EMS.definedBy, RES.doc_meter_metadata))
        if urn in hardware_by_urn:
            hardware = hardware_by_urn[urn]
            model_code = str(hardware["hardware_model_code"])
            graph.add((uri, EMS.hasHardwareModel, RES[f"hardware_{slug(model_code)}"]))
            graph.add((uri, EMS.hardwareModelCode, Literal(model_code)))
            graph.add((uri, EMS.sourceName, Literal(str(hardware.get("source_name", "")))))
            graph.add((uri, EMS.sourceTable, Literal(str(hardware.get("source_table", "")))))
            graph.add((uri, EMS.sourceDescription, Literal(str(hardware.get("source_description", "")))))
            graph.add((uri, EMS.definedBy, RES.doc_nature_scientific_data_2025))
        graph.add((uri, EMS.visualizedBy, RES.view_ems_meter_system_all))
        if group_code == "central_cooling":
            graph.add((uri, EMS.visualizedBy, RES.view_focus_central_cooling))
        if group_code == "server_power":
            graph.add((uri, EMS.visualizedBy, RES.view_focus_server_power))
        if group_code == "emission_lab":
            graph.add((uri, EMS.visualizedBy, RES.view_focus_emission_lab))

    for rec in redundancy_records:
        primary_urn = rec["primary_meter_urn"]
        redundant_urn = rec["redundant_meter_urn"]
        pair_uri = RES[f"redundancy_{slug(primary_urn)}__{slug(redundant_urn)}"]
        primary = meter_uri_by_urn[primary_urn]
        redundant = meter_uri_by_urn[redundant_urn]
        graph.add((pair_uri, RDF.type, OWL.NamedIndividual))
        graph.add((pair_uri, RDF.type, EMS.RedundancyPair))
        graph.add((pair_uri, RDFS.label, Literal(f"{primary_urn} - {redundant_urn}")))
        graph.add((pair_uri, EMS.hasPrimaryMeter, primary))
        graph.add((pair_uri, EMS.hasRedundantMeter, redundant))
        graph.add((pair_uri, EMS.hasGroup, RES[f"group_{slug(rec['equipment_group'])}"]))
        graph.add((pair_uri, EMS.equipmentName, Literal(rec["equipment_name"])))
        graph.add((pair_uri, EMS.definedBy, RES.doc_meter_metadata))
        graph.add((pair_uri, EMS.visualizedBy, RES.view_focus_redundancy))
        graph.add((primary, EMS.redundantWith, redundant))
        graph.add((redundant, EMS.redundantWith, primary))
        graph.add((primary, EMS.visualizedBy, RES.view_focus_redundancy))
        graph.add((redundant, EMS.visualizedBy, RES.view_focus_redundancy))

    return graph


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate EMS ontology artifacts")
    parser.add_argument(
        "--source",
        choices=["markdown", "db"],
        default="db",
        help="Metadata source. DB source is read-only and requires approved metadata tables.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    graph = build_graph(source=args.source)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(OUTPUT_PATH), format="turtle")
    graph.serialize(destination=str(PROTEGE_OUTPUT_PATH), format="xml")
    print({
        "source": args.source,
        "path": str(OUTPUT_PATH.relative_to(ROOT)),
        "protege_path": str(PROTEGE_OUTPUT_PATH.relative_to(ROOT)),
        "triples": len(graph),
    })


if __name__ == "__main__":
    main()
