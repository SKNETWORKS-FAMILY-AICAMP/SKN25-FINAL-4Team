#!/usr/bin/env python3
"""Validate ontology artifacts.

This script validates the generated RDF artifacts without reading DB
credentials or measurement rows. It checks graph parseability, schema
declarations, expected class counts, required meter relationships, redundancy
consistency, and selected analysis-facing query invariants.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS

try:
    from pyshacl import validate as pyshacl_validate
except ImportError:  # pragma: no cover - exercised only when dependency is missing
    pyshacl_validate = None

ROOT = Path(__file__).resolve().parents[2]
TTL_PATH = ROOT / "docs/ontology/ems.ttl"
PROTEGE_PATH = ROOT / "docs/ontology/ems_protege.owl"
SHAPES_PATH = ROOT / "docs/ontology/ems_shapes.ttl"

EMS = Namespace("https://nousresearch.local/ems/ontology#")
RES = Namespace("https://nousresearch.local/ems/resource/")

EXPECTED_COUNTS = {
    "ttl_triples": 2438,
    "protege_triples": 2438,
    "owl_ontology": 1,
    "owl_named_individual": 147,
    "classes": 17,
    "object_properties": 14,
    "data_properties": 13,
    "meters": 81,
    "electricity_meters": 71,
    "thermal_meters": 9,
    "weather_meters": 1,
    "equipment_groups": 17,
    "buildings": 6,
    "meter_roles": 4,
    "redundancy_pairs": 12,
    "visualization_views": 8,
    "metadata_documents": 3,
    "server_power_meters": 13,
    "central_cooling_meters": 5,
    "emission_lab_meters": 14,
    "server_power_redundancy_pairs": 4,
    "sign_convention_triples": 89,
    "anomaly_priority_triples": 102,
    "primary_view_triples": 17,
    "group_visualized_by_triples": 43,
    "equipment_layer_triples": 4,
}

EXPECTED_CLASSES = {
    "AnomalyEvent",
    "AnomalyPriorityLevel",
    "Building",
    "ElectricityMeter",
    "EquipmentGroup",
    "Feature",
    "FeatureRule",
    "MetadataDocument",
    "Meter",
    "MeterRole",
    "MeterDomain",
    "RedundancyPair",
    "RedundancyPolicy",
    "SignConvention",
    "ThermalMeter",
    "VisualizationView",
    "WeatherMeter",
}

EXPECTED_OBJECT_PROPERTIES = {
    "belongsToGroup",
    "definedBy",
    "hasGroup",
    "hasPrimaryMeter",
    "hasRedundantMeter",
    "hasRole",
    "hasDomain",
    "hasSignConvention",
    "hasAnomalyPriorityLevel",
    "locatedInBuilding",
    "redundantWith",
    "visualizedBy",
    "usesRedundancyPolicy",
    "usesMeterSetRule",
}

EXPECTED_DATA_PROPERTIES = {
    "anomalyPriority",
    "buildingCode",
    "equipmentGroupCode",
    "equipmentLayer",
    "equipmentName",
    "meterCount",
    "meterDomain",
    "meterRoleCode",
    "meterUrn",
    "noteFile",
    "primaryView",
    "signConvention",
    "sourcePath",
}

EXPECTED_ROLE_SIGN_CONVENTIONS = {
    "consumption": "positive_consumption_negative_quality_candidate",
    "production": "negative_production_positive_noise_or_reverse_flow",
    "thermal_flow": "direction_depends_on_equipment_context",
    "weather": "no_sign_convention",
}


@dataclass(frozen=True)
class ValidationResult:
    name: str
    expected: object
    actual: object
    ok: bool


def unique_subjects(graph: Graph, predicate: URIRef, obj: URIRef) -> set[URIRef]:
    return {subject for subject in graph.subjects(predicate, obj) if isinstance(subject, URIRef)}


def unique_objects(graph: Graph, subject: URIRef, predicate: URIRef) -> set[URIRef]:
    return {obj for obj in graph.objects(subject, predicate) if isinstance(obj, URIRef)}


def labels(graph: Graph, resources: Iterable[URIRef]) -> set[str]:
    values: set[str] = set()
    for resource in resources:
        for label in graph.objects(resource, RDFS.label):
            values.add(str(label))
    return values


def local_names(graph: Graph, rdf_type: URIRef) -> set[str]:
    prefix = str(EMS)
    return {
        str(subject).removeprefix(prefix)
        for subject in graph.subjects(RDF.type, rdf_type)
        if isinstance(subject, URIRef) and str(subject).startswith(prefix)
    }


def check_count(results: list[ValidationResult], name: str, expected: object, actual: object) -> None:
    results.append(ValidationResult(name=name, expected=expected, actual=actual, ok=actual == expected))


def parse_graph(path: Path, fmt: str) -> Graph:
    if not path.exists():
        raise FileNotFoundError(path)
    graph = Graph()
    graph.parse(path, format=fmt)
    return graph


def validate_counts(ttl_graph: Graph, protege_graph: Graph) -> list[ValidationResult]:
    results: list[ValidationResult] = []

    check_count(results, "ttl_triples", EXPECTED_COUNTS["ttl_triples"], len(ttl_graph))
    check_count(results, "protege_triples", EXPECTED_COUNTS["protege_triples"], len(protege_graph))
    check_count(results, "owl_ontology", EXPECTED_COUNTS["owl_ontology"], len(unique_subjects(ttl_graph, RDF.type, OWL.Ontology)))
    check_count(results, "owl_named_individual", EXPECTED_COUNTS["owl_named_individual"], len(unique_subjects(ttl_graph, RDF.type, OWL.NamedIndividual)))
    check_count(results, "classes", EXPECTED_COUNTS["classes"], len(local_names(ttl_graph, OWL.Class)))
    check_count(results, "object_properties", EXPECTED_COUNTS["object_properties"], len(local_names(ttl_graph, OWL.ObjectProperty)))
    check_count(results, "data_properties", EXPECTED_COUNTS["data_properties"], len(local_names(ttl_graph, OWL.DatatypeProperty)))
    check_count(results, "meters", EXPECTED_COUNTS["meters"], len(unique_subjects(ttl_graph, RDF.type, EMS.Meter)))
    check_count(results, "electricity_meters", EXPECTED_COUNTS["electricity_meters"], len(unique_subjects(ttl_graph, RDF.type, EMS.ElectricityMeter)))
    check_count(results, "thermal_meters", EXPECTED_COUNTS["thermal_meters"], len(unique_subjects(ttl_graph, RDF.type, EMS.ThermalMeter)))
    check_count(results, "weather_meters", EXPECTED_COUNTS["weather_meters"], len(unique_subjects(ttl_graph, RDF.type, EMS.WeatherMeter)))
    check_count(results, "equipment_groups", EXPECTED_COUNTS["equipment_groups"], len(unique_subjects(ttl_graph, RDF.type, EMS.EquipmentGroup)))
    check_count(results, "buildings", EXPECTED_COUNTS["buildings"], len(unique_subjects(ttl_graph, RDF.type, EMS.Building)))
    check_count(results, "meter_roles", EXPECTED_COUNTS["meter_roles"], len(unique_subjects(ttl_graph, RDF.type, EMS.MeterRole)))
    check_count(results, "redundancy_pairs", EXPECTED_COUNTS["redundancy_pairs"], len(unique_subjects(ttl_graph, RDF.type, EMS.RedundancyPair)))
    check_count(results, "visualization_views", EXPECTED_COUNTS["visualization_views"], len(unique_subjects(ttl_graph, RDF.type, EMS.VisualizationView)))
    check_count(results, "metadata_documents", EXPECTED_COUNTS["metadata_documents"], len(unique_subjects(ttl_graph, RDF.type, EMS.MetadataDocument)))
    return results


def validate_schema_declarations(graph: Graph) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    check_count(results, "declared_classes", EXPECTED_CLASSES, local_names(graph, OWL.Class))
    check_count(results, "declared_object_properties", EXPECTED_OBJECT_PROPERTIES, local_names(graph, OWL.ObjectProperty))
    check_count(results, "declared_data_properties", EXPECTED_DATA_PROPERTIES, local_names(graph, OWL.DatatypeProperty))
    return results


def validate_meter_completeness(graph: Graph) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    meters = unique_subjects(graph, RDF.type, EMS.Meter)

    missing_group = []
    missing_building = []
    missing_role = []
    missing_domain = []
    missing_urn = []
    missing_note = []
    missing_sign = []
    missing_priority = []

    multi_group = []
    multi_building = []
    multi_role = []

    for meter in sorted(meters):
        groups = list(graph.objects(meter, EMS.belongsToGroup))
        buildings = list(graph.objects(meter, EMS.locatedInBuilding))
        roles = list(graph.objects(meter, EMS.hasRole))
        domains = list(graph.objects(meter, EMS.meterDomain))
        urns = list(graph.objects(meter, EMS.meterUrn))
        notes = list(graph.objects(meter, EMS.noteFile))
        signs = list(graph.objects(meter, EMS.signConvention))
        priorities = list(graph.objects(meter, EMS.anomalyPriority))

        if not groups:
            missing_group.append(meter)
        if not buildings:
            missing_building.append(meter)
        if not roles:
            missing_role.append(meter)
        if not domains:
            missing_domain.append(meter)
        if not urns:
            missing_urn.append(meter)
        if not notes:
            missing_note.append(meter)
        if not signs:
            missing_sign.append(meter)
        if not priorities:
            missing_priority.append(meter)
        if len(groups) != 1:
            multi_group.append(meter)
        if len(buildings) != 1:
            multi_building.append(meter)
        if len(roles) != 1:
            multi_role.append(meter)

    check_count(results, "meters_missing_group", 0, len(missing_group))
    check_count(results, "meters_missing_building", 0, len(missing_building))
    check_count(results, "meters_missing_role", 0, len(missing_role))
    check_count(results, "meters_missing_domain", 0, len(missing_domain))
    check_count(results, "meters_missing_urn", 0, len(missing_urn))
    check_count(results, "meters_missing_note_file", 0, len(missing_note))
    check_count(results, "meters_missing_sign_convention", 0, len(missing_sign))
    check_count(results, "meters_missing_anomaly_priority", 0, len(missing_priority))
    check_count(results, "meters_with_non_single_group", 0, len(multi_group))
    check_count(results, "meters_with_non_single_building", 0, len(multi_building))
    check_count(results, "meters_with_non_single_role", 0, len(multi_role))
    return results


def validate_redundancy(graph: Graph) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    pairs = unique_subjects(graph, RDF.type, EMS.RedundancyPair)
    meters = unique_subjects(graph, RDF.type, EMS.Meter)

    missing_primary = []
    missing_redundant = []
    missing_group = []
    non_meter_endpoint = []
    missing_forward_relation = []
    missing_reverse_relation = []

    for pair in sorted(pairs):
        primary_values = unique_objects(graph, pair, EMS.hasPrimaryMeter)
        redundant_values = unique_objects(graph, pair, EMS.hasRedundantMeter)
        group_values = unique_objects(graph, pair, EMS.hasGroup)

        if len(primary_values) != 1:
            missing_primary.append(pair)
        if len(redundant_values) != 1:
            missing_redundant.append(pair)
        if len(group_values) != 1:
            missing_group.append(pair)
        if len(primary_values) != 1 or len(redundant_values) != 1:
            continue

        primary = next(iter(primary_values))
        redundant = next(iter(redundant_values))
        if primary not in meters or redundant not in meters:
            non_meter_endpoint.append(pair)
        if (primary, EMS.redundantWith, redundant) not in graph:
            missing_forward_relation.append(pair)
        if (redundant, EMS.redundantWith, primary) not in graph:
            missing_reverse_relation.append(pair)

    check_count(results, "redundancy_pairs_missing_primary", 0, len(missing_primary))
    check_count(results, "redundancy_pairs_missing_redundant", 0, len(missing_redundant))
    check_count(results, "redundancy_pairs_missing_group", 0, len(missing_group))
    check_count(results, "redundancy_pairs_with_non_meter_endpoint", 0, len(non_meter_endpoint))
    check_count(results, "redundancy_pairs_missing_forward_relation", 0, len(missing_forward_relation))
    check_count(results, "redundancy_pairs_missing_reverse_relation", 0, len(missing_reverse_relation))
    check_count(results, "redundantWith_triples", 24, len(list(graph.triples((None, EMS.redundantWith, None)))))
    return results


def validate_analysis_invariants(graph: Graph) -> list[ValidationResult]:
    results: list[ValidationResult] = []

    group_server_power = RES.group_server_power
    group_central_cooling = RES.group_central_cooling
    group_emission_lab = RES.group_emission_lab

    def meters_in_group(group: URIRef) -> set[URIRef]:
        return {subject for subject in graph.subjects(EMS.belongsToGroup, group) if isinstance(subject, URIRef)}

    server_power_meters = meters_in_group(group_server_power)
    central_cooling_meters = meters_in_group(group_central_cooling)
    emission_lab_meters = meters_in_group(group_emission_lab)

    server_power_pairs = {
        subject
        for subject in graph.subjects(EMS.hasGroup, group_server_power)
        if (subject, RDF.type, EMS.RedundancyPair) in graph
    }

    check_count(results, "server_power_meters", EXPECTED_COUNTS["server_power_meters"], len(server_power_meters))
    check_count(results, "central_cooling_meters", EXPECTED_COUNTS["central_cooling_meters"], len(central_cooling_meters))
    check_count(results, "emission_lab_meters", EXPECTED_COUNTS["emission_lab_meters"], len(emission_lab_meters))
    check_count(results, "server_power_redundancy_pairs", EXPECTED_COUNTS["server_power_redundancy_pairs"], len(server_power_pairs))

    h2_z64 = RES.meter_H2_Z64
    h2_ze64 = RES.meter_H2_ZE64
    check_count(results, "h2_z64_exists", True, (h2_z64, RDF.type, EMS.Meter) in graph)
    check_count(results, "h2_z64_group", {"server_power"}, labels(graph, graph.objects(h2_z64, EMS.belongsToGroup)))
    check_count(results, "h2_z64_building", {"H2"}, labels(graph, graph.objects(h2_z64, EMS.locatedInBuilding)))
    check_count(results, "h2_z64_role", {"consumption"}, labels(graph, graph.objects(h2_z64, EMS.hasRole)))
    check_count(results, "h2_z64_sign_convention", {"positive_consumption_negative_quality_candidate"}, set(str(value) for value in graph.objects(h2_z64, EMS.signConvention)))
    check_count(results, "h2_z64_anomaly_priority", {"2"}, set(str(value) for value in graph.objects(h2_z64, EMS.anomalyPriority)))
    check_count(results, "h2_z64_redundant_with_h2_ze64", True, (h2_z64, EMS.redundantWith, h2_ze64) in graph)
    check_count(results, "h2_ze64_redundant_with_h2_z64", True, (h2_ze64, EMS.redundantWith, h2_z64) in graph)
    check_count(results, "h2_z64_has_all_canvas", True, (h2_z64, EMS.visualizedBy, RES.view_ems_meter_system_all) in graph)
    check_count(results, "h2_z64_has_server_canvas", True, (h2_z64, EMS.visualizedBy, RES.view_focus_server_power) in graph)
    check_count(results, "h2_z64_has_redundancy_canvas", True, (h2_z64, EMS.visualizedBy, RES.view_focus_redundancy) in graph)
    return results


def validate_operational_mappings(graph: Graph) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    group_visualized_by_triples = [
        subject
        for subject, _, _ in graph.triples((None, EMS.visualizedBy, None))
        if (subject, RDF.type, EMS.EquipmentGroup) in graph
    ]
    role_sign_conventions = {
        str(label): str(sign)
        for role in unique_subjects(graph, RDF.type, EMS.MeterRole)
        for label in graph.objects(role, RDFS.label)
        for sign in graph.objects(role, EMS.signConvention)
    }

    check_count(results, "sign_convention_triples", EXPECTED_COUNTS["sign_convention_triples"], len(list(graph.triples((None, EMS.signConvention, None)))))
    check_count(results, "anomaly_priority_triples", EXPECTED_COUNTS["anomaly_priority_triples"], len(list(graph.triples((None, EMS.anomalyPriority, None)))))
    check_count(results, "primary_view_triples", EXPECTED_COUNTS["primary_view_triples"], len(list(graph.triples((None, EMS.primaryView, None)))))
    check_count(results, "group_visualized_by_triples", EXPECTED_COUNTS["group_visualized_by_triples"], len(group_visualized_by_triples))
    check_count(results, "equipment_layer_triples", EXPECTED_COUNTS["equipment_layer_triples"], len(list(graph.triples((None, EMS.equipmentLayer, None)))))
    check_count(results, "role_sign_conventions", EXPECTED_ROLE_SIGN_CONVENTIONS, role_sign_conventions)
    check_count(results, "emission_lab_feed_layers", {"feed"}, set(str(value) for value in graph.objects(RES.meter_H1_Z15, EMS.equipmentLayer)))
    check_count(results, "emission_lab_distribution_layers", {"distribution"}, set(str(value) for value in graph.objects(RES.meter_H1_Z17, EMS.equipmentLayer)))
    check_count(results, "server_power_group_priority", {"2"}, set(str(value) for value in graph.objects(RES.group_server_power, EMS.anomalyPriority)))
    check_count(results, "weather_station_group_priority", {"4"}, set(str(value) for value in graph.objects(RES.group_weather_station, EMS.anomalyPriority)))
    check_count(results, "server_power_group_has_focus_view", True, (RES.group_server_power, EMS.visualizedBy, RES.view_focus_server_power) in graph)
    check_count(results, "server_power_group_has_redundancy_view", True, (RES.group_server_power, EMS.visualizedBy, RES.view_focus_redundancy) in graph)
    return results


def validate_shacl() -> list[ValidationResult]:
    if pyshacl_validate is None:
        return [ValidationResult("shacl_pyshacl_installed", True, False, False)]
    if not SHAPES_PATH.exists():
        return [ValidationResult("shacl_shapes_file_exists", True, False, False)]

    conforms, _, report_text = pyshacl_validate(
        data_graph=str(TTL_PATH),
        shacl_graph=str(SHAPES_PATH),
        data_graph_format="turtle",
        shacl_graph_format="turtle",
        inference="none",
        advanced=True,
    )
    if not conforms:
        print(report_text)
    return [ValidationResult("shacl_conforms", True, bool(conforms), bool(conforms))]


def print_results(results: list[ValidationResult]) -> None:
    for result in results:
        status = "PASS" if result.ok else "FAIL"
        print(f"{status} {result.name}: expected={result.expected!r} actual={result.actual!r}")


def main() -> int:
    ttl_graph = parse_graph(TTL_PATH, "turtle")
    protege_graph = parse_graph(PROTEGE_PATH, "xml")

    results: list[ValidationResult] = []
    results.extend(validate_counts(ttl_graph, protege_graph))
    results.extend(validate_schema_declarations(ttl_graph))
    results.extend(validate_meter_completeness(ttl_graph))
    results.extend(validate_redundancy(ttl_graph))
    results.extend(validate_analysis_invariants(ttl_graph))
    results.extend(validate_operational_mappings(ttl_graph))
    results.extend(validate_shacl())

    print({
        "ttl_path": str(TTL_PATH.relative_to(ROOT)),
        "protege_path": str(PROTEGE_PATH.relative_to(ROOT)),
        "shapes_path": str(SHAPES_PATH.relative_to(ROOT)),
        "checks": len(results),
    })
    print_results(results)

    failed = [result for result in results if not result.ok]
    if failed:
        print({"status": "failed", "failed_checks": len(failed)})
        return 1

    print({"status": "passed", "failed_checks": 0})
    return 0


if __name__ == "__main__":
    sys.exit(main())
