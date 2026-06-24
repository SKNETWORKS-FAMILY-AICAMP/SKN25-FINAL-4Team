"""CMS ontology helper API."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

CMS = Namespace("https://nousresearch.local/cms/ontology#")
RES = Namespace("https://nousresearch.local/cms/resource/")

SIGN_INTERPRETATIONS = {
    "positive_consumption_negative_quality_candidate": {
        "negative_value_interpretation": "quality_or_meter_error_candidate",
        "positive_value_interpretation": "consumption_or_inflow",
    },
    "negative_production_positive_noise_or_reverse_flow": {
        "negative_value_interpretation": "production_or_outflow",
        "positive_value_interpretation": "noise_or_reverse_flow_candidate",
    },
    "direction_depends_on_equipment_context": {
        "negative_value_interpretation": "equipment_direction_dependent",
        "positive_value_interpretation": "equipment_direction_dependent",
    },
    "no_sign_convention": {
        "negative_value_interpretation": "measured_value",
        "positive_value_interpretation": "measured_value",
    },
}

LIVE_REPLAY_RULES = [
    "current_tick_uses_only_past_data",
    "future_imputation_for_current_tick_forbidden",
    "post_hoc_anomaly_label_as_feature_forbidden",
]

T = TypeVar("T")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class CMSOntology:
    graph: Graph
    root: Path
    ttl_path: Path

    @classmethod
    def from_default(cls, root: str | Path | None = None) -> CMSOntology:
        project_root = Path(root).resolve() if root is not None else _project_root()
        return cls.from_ttl(project_root / "docs/ontology/schema.ttl", root=project_root)

    @classmethod
    def from_ttl(cls, ttl_path: str | Path, root: str | Path | None = None) -> CMSOntology:
        path = Path(ttl_path).resolve()
        project_root = Path(root).resolve() if root is not None else path.parents[2]
        graph = Graph()
        graph.parse(path, format="turtle")
        return cls(graph=graph, root=project_root, ttl_path=path)

    def get_meter_context(self, meter_urn: str) -> dict[str, object]:
        meter = self._meter_resource(meter_urn)
        group = self._single_uri(meter, CMS.belongsToGroup)
        building = self._single_uri(meter, CMS.locatedInBuilding)
        role = self._single_uri(meter, CMS.hasRole)
        return {
            "meter_urn": self._literal(meter, CMS.meterUrn),
            "domain": self._literal(meter, CMS.meterDomain),
            "role": self._resource_code(role, CMS.meterRoleCode),
            "group": self._resource_code(group, CMS.equipmentGroupCode),
            "building": self._resource_code(building, CMS.buildingCode),
            "equipment_name": self._literal(meter, CMS.equipmentName, default=""),
            "sign_convention": self._literal(meter, CMS.signConvention),
            "anomaly_priority": int(self._literal(meter, CMS.anomalyPriority)),
            "note_file": self._literal(meter, CMS.noteFile),
            "redundant_with": self._meter_urns(self.graph.objects(meter, CMS.redundantWith)),
            "views": self._view_paths(self.graph.objects(meter, CMS.visualizedBy)),
        }

    def get_feature_meter_set(
        self,
        *,
        group: str | None = None,
        building: str | None = None,
        domain: str | None = None,
        role: str | None = None,
        exclude_redundant: bool = False,
    ) -> list[str]:
        meters = self._subjects(RDF.type, CMS.Meter)
        if group is not None:
            group_resource = self._group_resource(group)
            meters = [meter for meter in meters if (meter, CMS.belongsToGroup, group_resource) in self.graph]
        if building is not None:
            building_resource = self._building_resource(building)
            meters = [meter for meter in meters if (meter, CMS.locatedInBuilding, building_resource) in self.graph]
        if domain is not None:
            meters = [meter for meter in meters if domain in self._literal_values(meter, CMS.meterDomain)]
        if role is not None:
            role_resource = self._role_resource(role)
            meters = [meter for meter in meters if (meter, CMS.hasRole, role_resource) in self.graph]
        if exclude_redundant:
            redundant_endpoints = self._redundant_endpoints(group=group)
            meters = [meter for meter in meters if meter not in redundant_endpoints]
        return self._meter_urns(meters)

    def get_sign_interpretation(self, meter_urn: str) -> dict[str, str]:
        context = self.get_meter_context(meter_urn)
        sign = str(context["sign_convention"])
        interpretation = SIGN_INTERPRETATIONS[sign]
        return {
            "meter_urn": str(context["meter_urn"]),
            "role": str(context["role"]),
            "sign_convention": sign,
            **interpretation,
        }

    def get_aggregate_policy(self, *, group: str, domain: str, role: str) -> dict[str, object]:
        self._group_resource(group)
        self._role_resource(role)
        return {
            "group": group,
            "domain": domain,
            "role": role,
            "exclude_redundant": True,
            "exclude_weather": domain != "weather",
            "redundancy_policies": self._local_names(self._subjects(RDF.type, CMS.RedundancyPolicy)),
            "feature_rules": self._local_names(self._subjects(RDF.type, CMS.FeatureRule)),
        }

    @staticmethod
    def classify_redundancy_anomaly(*, primary_anomalous: bool, redundant_anomalous: bool) -> str:
        if primary_anomalous and redundant_anomalous:
            return "equipment_candidate"
        if primary_anomalous or redundant_anomalous:
            return "meter_candidate"
        return "no_redundancy_anomaly"

    @staticmethod
    def get_live_replay_rules() -> list[str]:
        return list(LIVE_REPLAY_RULES)

    def _meter_resource(self, meter_urn: str) -> URIRef:
        matches = [
            subject
            for subject in self.graph.subjects(CMS.meterUrn, Literal(meter_urn))
            if isinstance(subject, URIRef) and (subject, RDF.type, CMS.Meter) in self.graph
        ]
        return self._single_from_set(matches, f"meter {meter_urn}")

    def _group_resource(self, group: str) -> URIRef:
        matches = [
            subject
            for subject in self.graph.subjects(CMS.equipmentGroupCode, Literal(group))
            if isinstance(subject, URIRef) and (subject, RDF.type, CMS.EquipmentGroup) in self.graph
        ]
        return self._single_from_set(matches, f"equipment group {group}")

    def _building_resource(self, building: str) -> URIRef:
        matches = [
            subject
            for subject in self.graph.subjects(CMS.buildingCode, Literal(building))
            if isinstance(subject, URIRef) and (subject, RDF.type, CMS.Building) in self.graph
        ]
        return self._single_from_set(matches, f"building {building}")

    def _role_resource(self, role: str) -> URIRef:
        matches = [
            subject
            for subject in self.graph.subjects(CMS.meterRoleCode, Literal(role))
            if isinstance(subject, URIRef) and (subject, RDF.type, CMS.MeterRole) in self.graph
        ]
        return self._single_from_set(matches, f"meter role {role}")

    def _redundant_endpoints(self, group: str | None = None) -> set[URIRef]:
        group_resource = self._group_resource(group) if group is not None else None
        endpoints: set[URIRef] = set()
        for pair in self._subjects(RDF.type, CMS.RedundancyPair):
            if group_resource is not None and (pair, CMS.hasGroup, group_resource) not in self.graph:
                continue
            endpoints.update(self._uri_set(self.graph.objects(pair, CMS.hasRedundantMeter)))
        return endpoints

    def _literal(self, subject: URIRef, predicate: URIRef, *, default: str | None = None) -> str:
        values = self._literal_values(subject, predicate)
        if not values and default is not None:
            return default
        return self._single_from_set(values, f"literal {predicate} for {subject}")

    def _literal_values(self, subject: URIRef, predicate: URIRef) -> list[str]:
        return sorted(str(obj) for obj in self.graph.objects(subject, predicate))

    def _resource_code(self, resource: URIRef, code_predicate: URIRef) -> str:
        values = self._literal_values(resource, code_predicate)
        if values:
            return self._single_from_set(values, f"code {code_predicate} for {resource}")
        return self._single_from_set(self._literal_values(resource, RDFS.label), f"label for {resource}")

    def _view_paths(self, views: Iterable[URIRef]) -> list[str]:
        paths: list[str] = []
        for view in views:
            if isinstance(view, URIRef):
                paths.extend(self._literal_values(view, CMS.sourcePath))
        return sorted(paths)

    def _meter_urns(self, meters: Iterable[URIRef]) -> list[str]:
        urns = [self._literal(meter, CMS.meterUrn) for meter in meters if isinstance(meter, URIRef) and (meter, RDF.type, CMS.Meter) in self.graph]
        return sorted(urns, key=_natural_key)

    def _single_uri(self, subject: URIRef, predicate: URIRef) -> URIRef:
        return self._single_from_set(self._uri_set(self.graph.objects(subject, predicate)), f"URI {predicate} for {subject}")

    def _subjects(self, predicate: URIRef, obj: URIRef) -> list[URIRef]:
        return sorted([subject for subject in self.graph.subjects(predicate, obj) if isinstance(subject, URIRef)], key=str)

    @staticmethod
    def _uri_set(values: Iterable[object]) -> set[URIRef]:
        return {value for value in values if isinstance(value, URIRef)}

    @staticmethod
    def _local_names(resources: Iterable[URIRef]) -> list[str]:
        return sorted(re.split(r"[#/]", str(resource))[-1] for resource in resources)

    @staticmethod
    def _single_from_set(values: Iterable[T], description: str) -> T:
        items = list(values)
        if len(items) == 1:
            return items[0]
        if not items:
            raise KeyError(f"not found: {description}")
        raise ValueError(f"expected one {description}, got {len(items)}")


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value))
