"""EMS ontology helper API.

The module loads the generated EMS RDF/Turtle artifact and exposes small Python
helpers for analysis code. Measurement rows remain in PostgreSQL/TimescaleDB or
Parquet storage; this module only resolves metadata relationships such as meter
group, building, role, redundancy, and visualization views.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS

EMS = Namespace("https://nousresearch.local/ems/ontology#")
RES = Namespace("https://nousresearch.local/ems/resource/")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class EMSOntology:
    """Read-only helper around the generated EMS ontology graph."""

    graph: Graph
    root: Path
    ttl_path: Path

    @classmethod
    def from_default(cls, root: str | Path | None = None) -> "EMSOntology":
        """Load `docs/ontology/ems.ttl` from an EMS project root."""
        project_root = Path(root).resolve() if root is not None else _project_root()
        ttl_path = project_root / "docs/ontology/ems.ttl"
        return cls.from_ttl(ttl_path=ttl_path, root=project_root)

    @classmethod
    def from_ttl(cls, ttl_path: str | Path, root: str | Path | None = None) -> "EMSOntology":
        """Load an EMS ontology Turtle file."""
        path = Path(ttl_path).resolve()
        project_root = Path(root).resolve() if root is not None else path.parents[2]
        graph = Graph()
        graph.parse(path, format="turtle")
        return cls(graph=graph, root=project_root, ttl_path=path)

    def get_meter_context(self, meter_urn: str) -> dict[str, object]:
        """Return analysis context for one meter URN."""
        meter = self._meter_resource(meter_urn)
        group = self._single_uri(meter, EMS.belongsToGroup)
        return {
            "meter_urn": self._literal(meter, EMS.meterUrn),
            "domain": self._literal(meter, EMS.meterDomain),
            "role": self._resource_code(self._single_uri(meter, EMS.hasRole), EMS.meterRoleCode),
            "group": self._resource_code(group, EMS.equipmentGroupCode),
            "building": self._resource_code(self._single_uri(meter, EMS.locatedInBuilding), EMS.buildingCode),
            "equipment_name": self._literal(meter, EMS.equipmentName, default=""),
            "sign_convention": self._literal(meter, EMS.signConvention),
            "anomaly_priority": int(self._literal(group, EMS.anomalyPriority)),
            "note_file": self._literal(meter, EMS.noteFile),
            "redundant_with": self._meter_urns(self.graph.objects(meter, EMS.redundantWith)),
            "views": self._view_paths(self.graph.objects(meter, EMS.visualizedBy)),
        }

    def get_group_meters(self, group: str) -> list[str]:
        """Return meter URNs in an equipment group."""
        group_resource = self._group_resource(group)
        return self._meter_urns(self.graph.subjects(EMS.belongsToGroup, group_resource))

    def get_building_meters(self, building: str) -> list[str]:
        """Return meter URNs in a building or zone."""
        building_resource = self._building_resource(building)
        return self._meter_urns(self.graph.subjects(EMS.locatedInBuilding, building_resource))

    def get_redundancy_pairs(self, group: str | None = None) -> list[dict[str, str]]:
        """Return redundancy pairs, optionally filtered by equipment group."""
        group_resource = self._group_resource(group) if group is not None else None
        rows: list[dict[str, str]] = []

        for pair in self._subjects(RDF.type, EMS.RedundancyPair):
            pair_groups = self._uri_set(self.graph.objects(pair, EMS.hasGroup))
            if group_resource is not None and group_resource not in pair_groups:
                continue

            primary = self._single_uri(pair, EMS.hasPrimaryMeter)
            redundant = self._single_uri(pair, EMS.hasRedundantMeter)
            pair_group = self._single_from_set(pair_groups, f"group for redundancy pair {pair}")
            rows.append(
                {
                    "primary_meter": self._literal(primary, EMS.meterUrn),
                    "redundant_meter": self._literal(redundant, EMS.meterUrn),
                    "group": self._resource_code(pair_group, EMS.equipmentGroupCode),
                }
            )

        return sorted(rows, key=lambda row: (row["group"], row["primary_meter"], row["redundant_meter"]))

    def get_feature_meter_set(
        self,
        *,
        group: str | None = None,
        building: str | None = None,
        domain: str | None = None,
        role: str | None = None,
        exclude_redundant: bool = False,
    ) -> list[str]:
        """Return meter URNs for feature construction filters."""
        meters = self._subjects(RDF.type, EMS.Meter)

        if group is not None:
            group_resource = self._group_resource(group)
            meters = [meter for meter in meters if (meter, EMS.belongsToGroup, group_resource) in self.graph]
        if building is not None:
            building_resource = self._building_resource(building)
            meters = [meter for meter in meters if (meter, EMS.locatedInBuilding, building_resource) in self.graph]
        if domain is not None:
            meters = [meter for meter in meters if str(domain) in self._literal_values(meter, EMS.meterDomain)]
        if role is not None:
            role_resource = self._role_resource(role)
            meters = [meter for meter in meters if (meter, EMS.hasRole, role_resource) in self.graph]
        if exclude_redundant:
            redundant_endpoints = self._redundant_endpoints(group=group)
            meters = [meter for meter in meters if meter not in redundant_endpoints]

        return self._meter_urns(meters)

    def get_visualization_views(self, meter_urn: str | None = None, *, group: str | None = None) -> list[str]:
        """Return visualization source paths for a meter or group."""
        if (meter_urn is None and group is None) or (meter_urn is not None and group is not None):
            raise ValueError("provide exactly one of meter_urn or group")

        resource = self._meter_resource(meter_urn) if meter_urn is not None else self._group_resource(group)
        return self._view_paths(self.graph.objects(resource, EMS.visualizedBy))

    def _meter_resource(self, meter_urn: str) -> URIRef:
        matches = [
            subject
            for subject in self.graph.subjects(EMS.meterUrn, Literal(meter_urn))
            if isinstance(subject, URIRef) and (subject, RDF.type, EMS.Meter) in self.graph
        ]
        return self._single_from_set(matches, f"meter {meter_urn}")

    def _group_resource(self, group: str | None) -> URIRef:
        if group is None:
            raise ValueError("group is required")
        matches = [
            subject
            for subject in self.graph.subjects(EMS.equipmentGroupCode, Literal(group))
            if isinstance(subject, URIRef) and (subject, RDF.type, EMS.EquipmentGroup) in self.graph
        ]
        return self._single_from_set(matches, f"equipment group {group}")

    def _building_resource(self, building: str) -> URIRef:
        matches = [
            subject
            for subject in self.graph.subjects(EMS.buildingCode, Literal(building))
            if isinstance(subject, URIRef) and (subject, RDF.type, EMS.Building) in self.graph
        ]
        return self._single_from_set(matches, f"building {building}")

    def _role_resource(self, role: str) -> URIRef:
        matches = [
            subject
            for subject in self.graph.subjects(EMS.meterRoleCode, Literal(role))
            if isinstance(subject, URIRef) and (subject, RDF.type, EMS.MeterRole) in self.graph
        ]
        return self._single_from_set(matches, f"meter role {role}")

    def _redundant_endpoints(self, group: str | None = None) -> set[URIRef]:
        group_resource = self._group_resource(group) if group is not None else None
        endpoints: set[URIRef] = set()
        for pair in self._subjects(RDF.type, EMS.RedundancyPair):
            if group_resource is not None and (pair, EMS.hasGroup, group_resource) not in self.graph:
                continue
            endpoints.update(self._uri_set(self.graph.objects(pair, EMS.hasRedundantMeter)))
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
        labels = self._literal_values(resource, RDFS.label)
        return self._single_from_set(labels, f"label for {resource}")

    def _view_paths(self, views: Iterable[URIRef]) -> list[str]:
        paths: list[str] = []
        for view in views:
            if not isinstance(view, URIRef):
                continue
            paths.extend(self._literal_values(view, EMS.sourcePath))
        return sorted(paths)

    def _meter_urns(self, meters: Iterable[URIRef]) -> list[str]:
        urns: list[str] = []
        for meter in meters:
            if isinstance(meter, URIRef) and (meter, RDF.type, EMS.Meter) in self.graph:
                urns.append(self._literal(meter, EMS.meterUrn))
        return sorted(urns, key=_natural_key)

    def _single_uri(self, subject: URIRef, predicate: URIRef) -> URIRef:
        return self._single_from_set(self._uri_set(self.graph.objects(subject, predicate)), f"URI {predicate} for {subject}")

    def _subjects(self, predicate: URIRef, obj: URIRef) -> list[URIRef]:
        return sorted(
            [subject for subject in self.graph.subjects(predicate, obj) if isinstance(subject, URIRef)],
            key=str,
        )

    @staticmethod
    def _uri_set(values: Iterable[object]) -> set[URIRef]:
        return {value for value in values if isinstance(value, URIRef)}

    @staticmethod
    def _single_from_set[T](values: Iterable[T], description: str) -> T:
        items = list(values)
        if len(items) == 1:
            return items[0]
        if not items:
            raise KeyError(f"not found: {description}")
        raise ValueError(f"expected one {description}, got {len(items)}")


def _natural_key(value: str) -> tuple[object, ...]:
    parts = re.split(r"(\d+)", value)
    return tuple(int(part) if part.isdigit() else part for part in parts)