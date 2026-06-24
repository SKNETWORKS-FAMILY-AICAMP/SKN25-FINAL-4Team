#!/usr/bin/env python3
"""Generate CMS RDF/Turtle ontology artifacts.

Default source:
- PostgreSQL/TimescaleDB `ontology` schema projection, with legacy `cms_metadata` registry fallback.

Optional local markdown source, only when source files are restored manually:
- docs/ontology/source_graph/계량기_*.md frontmatter
- docs/ontology/source_graph/그룹_*.md frontmatter
- docs/specs/meter_metadata.md redundancy table

Output:
- docs/ontology/schema.ttl
- docs/ontology/protege.owl
"""

from __future__ import annotations

from pathlib import Path
import argparse
import re
from rdflib import Graph, Namespace, Literal, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD

ROOT = Path(__file__).resolve().parents[2]
GRAPH_DIR = ROOT / "docs/ontology/source_graph"  # optional local source; absent in the active tree by default
SPEC_PATH = ROOT / "docs/specs/meter_metadata.md"
OUTPUT_PATH = ROOT / "docs/ontology/schema.ttl"
PROTEGE_OUTPUT_PATH = ROOT / "docs/ontology/protege.owl"

ONTOLOGY_IRI = URIRef("https://nousresearch.local/cms/ontology")
CMS = Namespace("https://nousresearch.local/cms/ontology#")
RES = Namespace("https://nousresearch.local/cms/resource/")

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
            "note_file": f"db:ontology.meter/group/{code}",
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
    graph.add((CMS[local_name], RDF.type, OWL.Class))
    graph.add((CMS[local_name], RDFS.label, Literal(label, lang="ko")))


def add_object_property(graph: Graph, local_name: str, domain, range_, label: str) -> None:
    graph.add((CMS[local_name], RDF.type, OWL.ObjectProperty))
    graph.add((CMS[local_name], RDFS.domain, domain))
    graph.add((CMS[local_name], RDFS.range, range_))
    graph.add((CMS[local_name], RDFS.label, Literal(label, lang="ko")))


def add_data_property(graph: Graph, local_name: str, domain, range_, label: str) -> None:
    graph.add((CMS[local_name], RDF.type, OWL.DatatypeProperty))
    graph.add((CMS[local_name], RDFS.domain, domain))
    graph.add((CMS[local_name], RDFS.range, range_))
    graph.add((CMS[local_name], RDFS.label, Literal(label, lang="ko")))


def build_graph(source: str = "db") -> Graph:
    hardware_records: list[dict[str, str]] = []
    if source == "db":
        from load_db import fetch_db_metadata

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
    graph.bind("cms", CMS)
    graph.bind("cmsres", RES)
    graph.bind("owl", OWL)
    graph.bind("rdfs", RDFS)
    graph.bind("xsd", XSD)

    graph.add((ONTOLOGY_IRI, RDF.type, OWL.Ontology))
    graph.add((ONTOLOGY_IRI, RDFS.label, Literal("CMS ontology", lang="en")))
    graph.add((ONTOLOGY_IRI, RDFS.label, Literal("CMS 온톨로지", lang="ko")))
    graph.add((ONTOLOGY_IRI, RDFS.comment, Literal("CMS 계량기 metadata, 설비 group, 건물, 역할, redundancy 관계를 정의한 ontology artifact", lang="ko")))

    for name, label in {
        "Meter": "CMS 계량기",
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
        graph.add((CMS[sub], RDFS.subClassOf, CMS[sup]))
    for left, right in [("ElectricityMeter", "ThermalMeter"), ("ElectricityMeter", "WeatherMeter"), ("ThermalMeter", "WeatherMeter")]:
        graph.add((CMS[left], OWL.disjointWith, CMS[right]))

    for name, domain, range_, label in [
        ("belongsToGroup", CMS.Meter, CMS.EquipmentGroup, "meter가 equipment group에 속함"),
        ("locatedInBuilding", CMS.Meter, CMS.Building, "meter가 building 또는 zone에 속함"),
        ("hasRole", CMS.Meter, CMS.MeterRole, "meter의 분석 역할"),
        ("redundantWith", CMS.Meter, CMS.Meter, "중복 계량 관계"),
        ("hasPrimaryMeter", CMS.RedundancyPair, CMS.Meter, "redundancy pair의 primary meter"),
        ("hasRedundantMeter", CMS.RedundancyPair, CMS.Meter, "redundancy pair의 redundant meter"),
        ("hasGroup", CMS.RedundancyPair, CMS.EquipmentGroup, "redundancy pair가 속한 group"),
        ("visualizedBy", OWL.Thing, CMS.VisualizationView, "entity를 확인할 수 있는 view"),
        ("definedBy", OWL.Thing, CMS.MetadataDocument, "entity 기준 문서"),
        ("hasDomain", CMS.Meter, CMS.MeterDomain, "meter의 계측 domain vocabulary"),
        ("hasSignConvention", OWL.Thing, CMS.SignConvention, "부호 해석 vocabulary"),
        ("hasAnomalyPriorityLevel", OWL.Thing, CMS.AnomalyPriorityLevel, "이상탐지 우선순위 vocabulary"),
        ("usesRedundancyPolicy", CMS.FeatureRule, CMS.RedundancyPolicy, "feature rule이 사용하는 redundancy policy"),
        ("usesMeterSetRule", CMS.Feature, CMS.FeatureRule, "feature가 사용하는 meter set rule"),
        ("hasHardwareModel", CMS.Meter, CMS.HardwareModel, "meter의 하드웨어 모델"),
    ]:
        add_object_property(graph, name, domain, range_, label)
    graph.add((CMS.redundantWith, RDF.type, OWL.SymmetricProperty))

    for name, domain, range_, label in [
        ("meterUrn", CMS.Meter, XSD.string, "DB meter identifier"),
        ("meterDomain", CMS.Meter, XSD.string, "계측 domain"),
        ("meterRoleCode", CMS.Meter, XSD.string, "계량기 role code"),
        ("equipmentGroupCode", OWL.Thing, XSD.string, "equipment group code"),
        ("equipmentName", OWL.Thing, XSD.string, "equipment name"),
        ("equipmentLayer", CMS.Meter, XSD.string, "equipment layer within a group"),
        ("buildingCode", OWL.Thing, XSD.string, "building 또는 zone code"),
        ("signConvention", OWL.Thing, XSD.string, "계량값 부호 해석 규칙"),
        ("anomalyPriority", OWL.Thing, XSD.integer, "이상탐지 검토 우선순위"),
        ("primaryView", CMS.EquipmentGroup, XSD.string, "group 기본 검토 view"),
        ("noteFile", OWL.Thing, XSD.string, "project-relative note path"),
        ("sourcePath", CMS.MetadataDocument, XSD.string, "project-relative source path"),
        ("meterCount", CMS.EquipmentGroup, XSD.integer, "group meter count"),
        ("hardwareModelCode", OWL.Thing, XSD.string, "계량기 하드웨어 모델 코드"),
        ("manufacturer", CMS.HardwareModel, XSD.string, "계량기 제조사"),
        ("modelName", CMS.HardwareModel, XSD.string, "계량기 모델명"),
        ("sourceName", OWL.Thing, XSD.string, "metadata source name"),
        ("sourceTable", OWL.Thing, XSD.string, "metadata source table"),
        ("sourceDescription", OWL.Thing, XSD.string, "metadata source description"),
    ]:
        add_data_property(graph, name, domain, range_, label)
    graph.add((CMS.meterUrn, RDF.type, OWL.FunctionalProperty))

    for name, source in {
        "meter_metadata": "docs/specs/meter_metadata.md",
        "ontology_lite": "docs/specs/ontology.md",
        "ontology_schema": "docs/specs/ontology.md",
        "nature_scientific_data_2025": "https://doi.org/10.1038/s41597-025-05186-3",
    }.items():
        uri = RES[f"doc_{name}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.MetadataDocument))
        graph.add((uri, RDFS.label, Literal(source)))
        graph.add((uri, CMS.sourcePath, Literal(source)))

    for key, source in {
        "cms_condition_monitoring_overview": "docs/specs/meter_metadata.md",
        "cms_condition_monitoring_all": "docs/specs/meter_metadata.md",
        "focus_central_cooling": "docs/specs/meter_metadata.md",
        "focus_server_power": "docs/specs/meter_metadata.md",
        "focus_emission_lab": "docs/specs/meter_metadata.md",
        "focus_redundancy": "docs/specs/meter_metadata.md",
        "meter_inventory_graph": "docs/specs/meter_metadata.md",
        "redundancy_graph": "docs/specs/meter_metadata.md",
    }.items():
        uri = RES[f"view_{key}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.VisualizationView))
        graph.add((uri, RDFS.label, Literal(key)))
        graph.add((uri, CMS.sourcePath, Literal(source)))

    for domain in ["electricity", "thermal", "weather"]:
        uri = RES[f"domain_{slug(domain)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.MeterDomain))
        graph.add((uri, RDFS.label, Literal(domain)))
        graph.add((uri, CMS.meterDomain, Literal(domain)))
        graph.add((uri, CMS.definedBy, RES.doc_meter_metadata))

    for sign in sorted(set(ROLE_SIGN_CONVENTIONS.values())):
        uri = RES[f"sign_{slug(sign)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.SignConvention))
        graph.add((uri, RDFS.label, Literal(sign)))
        graph.add((uri, CMS.signConvention, Literal(sign)))
        graph.add((uri, CMS.definedBy, RES.doc_meter_metadata))

    for priority in [1, 2, 3, 4]:
        uri = RES[f"priority_{priority}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.AnomalyPriorityLevel))
        graph.add((uri, RDFS.label, Literal(f"priority {priority}")))
        graph.add((uri, CMS.anomalyPriority, Literal(priority, datatype=XSD.integer)))
        graph.add((uri, CMS.definedBy, RES.doc_meter_metadata))

    for key, label in REDUNDANCY_POLICIES.items():
        uri = RES[f"policy_{slug(key)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.RedundancyPolicy))
        graph.add((uri, RDFS.label, Literal(label, lang="ko")))
        graph.add((uri, CMS.definedBy, RES.doc_meter_metadata))

    for key, label in FEATURE_RULES.items():
        uri = RES[f"rule_{slug(key)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.FeatureRule))
        graph.add((uri, RDFS.label, Literal(label, lang="ko")))
        graph.add((uri, CMS.definedBy, RES.doc_meter_metadata))
    graph.add((RES.rule_aggregate_excludes_redundant, CMS.usesRedundancyPolicy, RES.policy_exclude_redundant_endpoint))

    for role in sorted({str(m["meter_role"]) for m in meter_records}):
        uri = RES[f"role_{slug(role)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.MeterRole))
        graph.add((uri, RDFS.label, Literal(role)))
        graph.add((uri, CMS.meterRoleCode, Literal(role)))
        graph.add((uri, CMS.signConvention, Literal(ROLE_SIGN_CONVENTIONS[role])))
        graph.add((uri, CMS.hasSignConvention, RES[f"sign_{slug(ROLE_SIGN_CONVENTIONS[role])}"]))
        graph.add((uri, CMS.definedBy, RES.doc_meter_metadata))

    for building in sorted({str(m["building_code"]) for m in meter_records}):
        uri = RES[f"building_{slug(building)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.Building))
        graph.add((uri, RDFS.label, Literal(building)))
        graph.add((uri, CMS.buildingCode, Literal(building)))
        graph.add((uri, CMS.definedBy, RES.doc_meter_metadata))

    for rec in group_records:
        code = str(rec["equipment_group"])
        uri = RES[f"group_{slug(code)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.EquipmentGroup))
        graph.add((uri, RDFS.label, Literal(code)))
        graph.add((uri, CMS.equipmentGroupCode, Literal(code)))
        meter_count = rec.get("meter_count")
        if str(meter_count).isdigit():
            graph.add((uri, CMS.meterCount, Literal(int(str(meter_count)), datatype=XSD.integer)))
        if rec.get("primary_view"):
            graph.add((uri, CMS.primaryView, Literal(str(rec["primary_view"]))))
        graph.add((uri, CMS.anomalyPriority, Literal(GROUP_ANOMALY_PRIORITIES[code], datatype=XSD.integer)))
        graph.add((uri, CMS.hasAnomalyPriorityLevel, RES[f"priority_{GROUP_ANOMALY_PRIORITIES[code]}"]))
        graph.add((uri, CMS.noteFile, Literal(str(rec["note_file"]))))
        graph.add((uri, CMS.definedBy, RES.doc_meter_metadata))
        graph.add((uri, CMS.visualizedBy, RES.view_cms_meter_system_overview))
        graph.add((uri, CMS.visualizedBy, RES.view_meter_inventory_graph))
        if code in FOCUSED_VIEW_BY_GROUP:
            graph.add((uri, CMS.visualizedBy, FOCUSED_VIEW_BY_GROUP[code]))
        if code in redundancy_group_codes:
            graph.add((uri, CMS.visualizedBy, RES.view_focus_redundancy))

    hardware_by_urn = {str(rec["meter_urn"]): rec for rec in hardware_records}
    hardware_model_records = {
        str(rec["hardware_model_code"]): rec
        for rec in hardware_records
    }
    for model_code, rec in sorted(hardware_model_records.items()):
        uri = RES[f"hardware_{slug(model_code)}"]
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.HardwareModel))
        graph.add((uri, RDFS.label, Literal(model_code)))
        graph.add((uri, CMS.hardwareModelCode, Literal(model_code)))
        graph.add((uri, CMS.manufacturer, Literal(str(rec.get("manufacturer", "")))))
        graph.add((uri, CMS.modelName, Literal(str(rec.get("model_name", "")))))
        graph.add((uri, CMS.meterDomain, Literal(str(rec.get("meter_category", "")))))
        graph.add((uri, CMS.definedBy, RES.doc_nature_scientific_data_2025))

    meter_uri_by_urn = {}
    for rec in meter_records:
        urn = str(rec["meter_urn"])
        domain = str(rec["meter_domain"])
        role = str(rec["meter_role"])
        group_code = str(rec["equipment_group"])
        building = str(rec["building_code"])
        uri = RES[f"meter_{slug(urn)}"]
        meter_uri_by_urn[urn] = uri
        meter_class = {"electricity": CMS.ElectricityMeter, "thermal": CMS.ThermalMeter, "weather": CMS.WeatherMeter}.get(domain, CMS.Meter)
        graph.add((uri, RDF.type, OWL.NamedIndividual))
        graph.add((uri, RDF.type, CMS.Meter))
        graph.add((uri, RDF.type, meter_class))
        graph.add((uri, RDFS.label, Literal(urn)))
        graph.add((uri, CMS.meterUrn, Literal(urn)))
        graph.add((uri, CMS.meterDomain, Literal(domain)))
        graph.add((uri, CMS.hasDomain, RES[f"domain_{slug(domain)}"]))
        graph.add((uri, CMS.meterRoleCode, Literal(role)))
        graph.add((uri, CMS.equipmentGroupCode, Literal(group_code)))
        graph.add((uri, CMS.equipmentName, Literal(str(rec.get("equipment_name", "")))))
        if rec.get("equipment_layer"):
            graph.add((uri, CMS.equipmentLayer, Literal(str(rec["equipment_layer"]))))
        graph.add((uri, CMS.buildingCode, Literal(building)))
        graph.add((uri, CMS.signConvention, Literal(ROLE_SIGN_CONVENTIONS[role])))
        graph.add((uri, CMS.hasSignConvention, RES[f"sign_{slug(ROLE_SIGN_CONVENTIONS[role])}"]))
        graph.add((uri, CMS.anomalyPriority, Literal(GROUP_ANOMALY_PRIORITIES[group_code], datatype=XSD.integer)))
        graph.add((uri, CMS.hasAnomalyPriorityLevel, RES[f"priority_{GROUP_ANOMALY_PRIORITIES[group_code]}"]))
        graph.add((uri, CMS.noteFile, Literal(str(rec["note_file"]))))
        graph.add((uri, CMS.belongsToGroup, RES[f"group_{slug(group_code)}"]))
        graph.add((uri, CMS.locatedInBuilding, RES[f"building_{slug(building)}"]))
        graph.add((uri, CMS.hasRole, RES[f"role_{slug(role)}"]))
        graph.add((uri, CMS.definedBy, RES.doc_meter_metadata))
        if urn in hardware_by_urn:
            hardware = hardware_by_urn[urn]
            model_code = str(hardware["hardware_model_code"])
            graph.add((uri, CMS.hasHardwareModel, RES[f"hardware_{slug(model_code)}"]))
            graph.add((uri, CMS.hardwareModelCode, Literal(model_code)))
            graph.add((uri, CMS.sourceName, Literal(str(hardware.get("source_name", "")))))
            graph.add((uri, CMS.sourceTable, Literal(str(hardware.get("source_table", "")))))
            graph.add((uri, CMS.sourceDescription, Literal(str(hardware.get("source_description", "")))))
            graph.add((uri, CMS.definedBy, RES.doc_nature_scientific_data_2025))
        graph.add((uri, CMS.visualizedBy, RES.view_cms_meter_system_all))
        if group_code == "central_cooling":
            graph.add((uri, CMS.visualizedBy, RES.view_focus_central_cooling))
        if group_code == "server_power":
            graph.add((uri, CMS.visualizedBy, RES.view_focus_server_power))
        if group_code == "emission_lab":
            graph.add((uri, CMS.visualizedBy, RES.view_focus_emission_lab))

    for rec in redundancy_records:
        primary_urn = rec["primary_meter_urn"]
        redundant_urn = rec["redundant_meter_urn"]
        pair_uri = RES[f"redundancy_{slug(primary_urn)}__{slug(redundant_urn)}"]
        primary = meter_uri_by_urn[primary_urn]
        redundant = meter_uri_by_urn[redundant_urn]
        graph.add((pair_uri, RDF.type, OWL.NamedIndividual))
        graph.add((pair_uri, RDF.type, CMS.RedundancyPair))
        graph.add((pair_uri, RDFS.label, Literal(f"{primary_urn} - {redundant_urn}")))
        graph.add((pair_uri, CMS.hasPrimaryMeter, primary))
        graph.add((pair_uri, CMS.hasRedundantMeter, redundant))
        graph.add((pair_uri, CMS.hasGroup, RES[f"group_{slug(rec['equipment_group'])}"]))
        graph.add((pair_uri, CMS.equipmentName, Literal(rec["equipment_name"])))
        graph.add((pair_uri, CMS.definedBy, RES.doc_meter_metadata))
        graph.add((pair_uri, CMS.visualizedBy, RES.view_focus_redundancy))
        graph.add((primary, CMS.redundantWith, redundant))
        graph.add((redundant, CMS.redundantWith, primary))
        graph.add((primary, CMS.visualizedBy, RES.view_focus_redundancy))
        graph.add((redundant, CMS.visualizedBy, RES.view_focus_redundancy))

    return graph


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate CMS ontology artifacts")
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
