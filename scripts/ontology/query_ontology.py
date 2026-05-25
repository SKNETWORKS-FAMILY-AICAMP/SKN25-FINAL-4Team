#!/usr/bin/env python3
"""Run sample SPARQL queries against the ontology."""

from __future__ import annotations

from pathlib import Path
from rdflib import Graph

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_PATH = ROOT / "docs/ontology/ems.ttl"

PREFIX = """
PREFIX ems: <https://nousresearch.local/ems/ontology#>
PREFIX emsres: <https://nousresearch.local/ems/resource/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""


def run_query(graph: Graph, name: str, query: str, limit: int = 10) -> None:
    rows = list(graph.query(PREFIX + query))
    print(f"[{name}] rows={len(rows)}")
    for row in rows[:limit]:
        print("  " + " | ".join(str(value) for value in row))


def main() -> None:
    graph = Graph()
    graph.parse(ONTOLOGY_PATH, format="turtle")
    print({"ontology": str(ONTOLOGY_PATH.relative_to(ROOT)), "triples": len(graph)})

    run_query(graph, "group_meter_count", """
SELECT ?groupLabel (COUNT(?meter) AS ?meter_count)
WHERE {
  ?meter a ems:Meter ;
         ems:belongsToGroup ?group .
  ?group rdfs:label ?groupLabel .
}
GROUP BY ?groupLabel
ORDER BY DESC(?meter_count)
""")

    run_query(graph, "meter_completeness", """
SELECT ?meterLabel ?groupLabel ?buildingLabel ?roleLabel ?domain ?signConvention ?priority
WHERE {
  ?meter a ems:Meter ;
         rdfs:label ?meterLabel ;
         ems:belongsToGroup ?group ;
         ems:locatedInBuilding ?building ;
         ems:hasRole ?role ;
         ems:meterDomain ?domain ;
         ems:signConvention ?signConvention ;
         ems:anomalyPriority ?priority .
  ?group rdfs:label ?groupLabel .
  ?building rdfs:label ?buildingLabel .
  ?role rdfs:label ?roleLabel .
}
ORDER BY ?meterLabel
""")

    run_query(graph, "group_meter_list", """
SELECT ?groupLabel (COUNT(?meter) AS ?meter_count) (GROUP_CONCAT(?meterLabel; separator=", ") AS ?meters)
WHERE {
  ?meter a ems:Meter ;
         rdfs:label ?meterLabel ;
         ems:belongsToGroup ?group .
  ?group rdfs:label ?groupLabel .
}
GROUP BY ?groupLabel
ORDER BY ?groupLabel
""", limit=17)

    run_query(graph, "building_meter_count", """
SELECT ?buildingLabel (COUNT(?meter) AS ?meter_count)
WHERE {
  ?meter a ems:Meter ;
         ems:locatedInBuilding ?building .
  ?building rdfs:label ?buildingLabel .
}
GROUP BY ?buildingLabel
ORDER BY ?buildingLabel
""", limit=10)

    run_query(graph, "domain_role_distribution", """
SELECT ?domain ?roleLabel (COUNT(?meter) AS ?meter_count)
WHERE {
  ?meter a ems:Meter ;
         ems:meterDomain ?domain ;
         ems:hasRole ?role .
  ?role rdfs:label ?roleLabel .
}
GROUP BY ?domain ?roleLabel
ORDER BY ?domain ?roleLabel
""")

    run_query(graph, "hardware_model_distribution", """
SELECT ?hardwareLabel ?manufacturer ?modelName (COUNT(?meter) AS ?meter_count)
WHERE {
  ?meter a ems:Meter ;
         ems:hasHardwareModel ?hardware .
  ?hardware rdfs:label ?hardwareLabel ;
            ems:manufacturer ?manufacturer ;
            ems:modelName ?modelName .
}
GROUP BY ?hardwareLabel ?manufacturer ?modelName
ORDER BY DESC(?meter_count) ?hardwareLabel
""", limit=10)

    run_query(graph, "all_redundancy_pairs", """
SELECT ?groupLabel ?primaryLabel ?redundantLabel ?equipmentName
WHERE {
  ?pair a ems:RedundancyPair ;
        ems:hasGroup ?group ;
        ems:hasPrimaryMeter ?primary ;
        ems:hasRedundantMeter ?redundant ;
        ems:equipmentName ?equipmentName .
  ?group rdfs:label ?groupLabel .
  ?primary rdfs:label ?primaryLabel .
  ?redundant rdfs:label ?redundantLabel .
}
ORDER BY ?groupLabel ?primaryLabel
""", limit=12)

    run_query(graph, "server_power_redundancy", """
SELECT ?primaryLabel ?redundantLabel ?equipmentName
WHERE {
  ?pair a ems:RedundancyPair ;
        ems:hasGroup emsres:group_server_power ;
        ems:hasPrimaryMeter ?primary ;
        ems:hasRedundantMeter ?redundant ;
        ems:equipmentName ?equipmentName .
  ?primary rdfs:label ?primaryLabel .
  ?redundant rdfs:label ?redundantLabel .
}
ORDER BY ?primaryLabel
""")

    run_query(graph, "h2_z64_context", """
SELECT ?meterLabel ?groupLabel ?buildingLabel ?roleLabel ?signConvention ?priority ?relatedMeterLabel
WHERE {
  emsres:meter_H2_Z64 rdfs:label ?meterLabel ;
                       ems:belongsToGroup ?group ;
                       ems:locatedInBuilding ?building ;
                       ems:hasRole ?role ;
                       ems:signConvention ?signConvention ;
                       ems:anomalyPriority ?priority .
  ?group rdfs:label ?groupLabel .
  ?building rdfs:label ?buildingLabel .
  ?role rdfs:label ?roleLabel .
  OPTIONAL {
    emsres:meter_H2_Z64 ems:redundantWith ?relatedMeter .
    ?relatedMeter rdfs:label ?relatedMeterLabel .
  }
}
""")

    run_query(graph, "server_power_aggregate_meter_set", """
SELECT ?meterLabel
WHERE {
  ?meter a ems:Meter ;
         rdfs:label ?meterLabel ;
         ems:belongsToGroup emsres:group_server_power ;
         ems:meterDomain "electricity" ;
         ems:hasRole emsres:role_consumption .
  FILTER NOT EXISTS {
    ?pair a ems:RedundancyPair ;
          ems:hasGroup emsres:group_server_power ;
          ems:hasRedundantMeter ?meter .
  }
}
ORDER BY ?meterLabel
""")

    run_query(graph, "priority_group_views", """
SELECT ?priority ?groupLabel ?primaryView ?viewPath
WHERE {
  ?group a ems:EquipmentGroup ;
         rdfs:label ?groupLabel ;
         ems:anomalyPriority ?priority ;
         ems:primaryView ?primaryView ;
         ems:visualizedBy ?view .
  ?view ems:sourcePath ?viewPath .
}
ORDER BY ?priority ?groupLabel ?viewPath
""", limit=20)


if __name__ == "__main__":
    main()
