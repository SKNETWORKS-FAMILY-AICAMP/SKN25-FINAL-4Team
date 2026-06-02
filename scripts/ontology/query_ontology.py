#!/usr/bin/env python3
"""Run sample SPARQL queries against the ontology."""

from __future__ import annotations

from pathlib import Path
from rdflib import Graph

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY_PATH = ROOT / "docs/ontology/cms.ttl"

PREFIX = """
PREFIX cms: <https://nousresearch.local/cms/ontology#>
PREFIX cmsres: <https://nousresearch.local/cms/resource/>
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
  ?meter a cms:Meter ;
         cms:belongsToGroup ?group .
  ?group rdfs:label ?groupLabel .
}
GROUP BY ?groupLabel
ORDER BY DESC(?meter_count)
""")

    run_query(graph, "meter_completeness", """
SELECT ?meterLabel ?groupLabel ?buildingLabel ?roleLabel ?domain ?signConvention ?priority
WHERE {
  ?meter a cms:Meter ;
         rdfs:label ?meterLabel ;
         cms:belongsToGroup ?group ;
         cms:locatedInBuilding ?building ;
         cms:hasRole ?role ;
         cms:meterDomain ?domain ;
         cms:signConvention ?signConvention ;
         cms:anomalyPriority ?priority .
  ?group rdfs:label ?groupLabel .
  ?building rdfs:label ?buildingLabel .
  ?role rdfs:label ?roleLabel .
}
ORDER BY ?meterLabel
""")

    run_query(graph, "group_meter_list", """
SELECT ?groupLabel (COUNT(?meter) AS ?meter_count) (GROUP_CONCAT(?meterLabel; separator=", ") AS ?meters)
WHERE {
  ?meter a cms:Meter ;
         rdfs:label ?meterLabel ;
         cms:belongsToGroup ?group .
  ?group rdfs:label ?groupLabel .
}
GROUP BY ?groupLabel
ORDER BY ?groupLabel
""", limit=17)

    run_query(graph, "building_meter_count", """
SELECT ?buildingLabel (COUNT(?meter) AS ?meter_count)
WHERE {
  ?meter a cms:Meter ;
         cms:locatedInBuilding ?building .
  ?building rdfs:label ?buildingLabel .
}
GROUP BY ?buildingLabel
ORDER BY ?buildingLabel
""", limit=10)

    run_query(graph, "domain_role_distribution", """
SELECT ?domain ?roleLabel (COUNT(?meter) AS ?meter_count)
WHERE {
  ?meter a cms:Meter ;
         cms:meterDomain ?domain ;
         cms:hasRole ?role .
  ?role rdfs:label ?roleLabel .
}
GROUP BY ?domain ?roleLabel
ORDER BY ?domain ?roleLabel
""")

    run_query(graph, "hardware_model_distribution", """
SELECT ?hardwareLabel ?manufacturer ?modelName (COUNT(?meter) AS ?meter_count)
WHERE {
  ?meter a cms:Meter ;
         cms:hasHardwareModel ?hardware .
  ?hardware rdfs:label ?hardwareLabel ;
            cms:manufacturer ?manufacturer ;
            cms:modelName ?modelName .
}
GROUP BY ?hardwareLabel ?manufacturer ?modelName
ORDER BY DESC(?meter_count) ?hardwareLabel
""", limit=10)

    run_query(graph, "all_redundancy_pairs", """
SELECT ?groupLabel ?primaryLabel ?redundantLabel ?equipmentName
WHERE {
  ?pair a cms:RedundancyPair ;
        cms:hasGroup ?group ;
        cms:hasPrimaryMeter ?primary ;
        cms:hasRedundantMeter ?redundant ;
        cms:equipmentName ?equipmentName .
  ?group rdfs:label ?groupLabel .
  ?primary rdfs:label ?primaryLabel .
  ?redundant rdfs:label ?redundantLabel .
}
ORDER BY ?groupLabel ?primaryLabel
""", limit=12)

    run_query(graph, "server_power_redundancy", """
SELECT ?primaryLabel ?redundantLabel ?equipmentName
WHERE {
  ?pair a cms:RedundancyPair ;
        cms:hasGroup cmsres:group_server_power ;
        cms:hasPrimaryMeter ?primary ;
        cms:hasRedundantMeter ?redundant ;
        cms:equipmentName ?equipmentName .
  ?primary rdfs:label ?primaryLabel .
  ?redundant rdfs:label ?redundantLabel .
}
ORDER BY ?primaryLabel
""")

    run_query(graph, "h2_z64_context", """
SELECT ?meterLabel ?groupLabel ?buildingLabel ?roleLabel ?signConvention ?priority ?relatedMeterLabel
WHERE {
  cmsres:meter_H2_Z64 rdfs:label ?meterLabel ;
                       cms:belongsToGroup ?group ;
                       cms:locatedInBuilding ?building ;
                       cms:hasRole ?role ;
                       cms:signConvention ?signConvention ;
                       cms:anomalyPriority ?priority .
  ?group rdfs:label ?groupLabel .
  ?building rdfs:label ?buildingLabel .
  ?role rdfs:label ?roleLabel .
  OPTIONAL {
    cmsres:meter_H2_Z64 cms:redundantWith ?relatedMeter .
    ?relatedMeter rdfs:label ?relatedMeterLabel .
  }
}
""")

    run_query(graph, "server_power_aggregate_meter_set", """
SELECT ?meterLabel
WHERE {
  ?meter a cms:Meter ;
         rdfs:label ?meterLabel ;
         cms:belongsToGroup cmsres:group_server_power ;
         cms:meterDomain "electricity" ;
         cms:hasRole cmsres:role_consumption .
  FILTER NOT EXISTS {
    ?pair a cms:RedundancyPair ;
          cms:hasGroup cmsres:group_server_power ;
          cms:hasRedundantMeter ?meter .
  }
}
ORDER BY ?meterLabel
""")

    run_query(graph, "priority_group_views", """
SELECT ?priority ?groupLabel ?primaryView ?viewPath
WHERE {
  ?group a cms:EquipmentGroup ;
         rdfs:label ?groupLabel ;
         cms:anomalyPriority ?priority ;
         cms:primaryView ?primaryView ;
         cms:visualizedBy ?view .
  ?view cms:sourcePath ?viewPath .
}
ORDER BY ?priority ?groupLabel ?viewPath
""", limit=20)


if __name__ == "__main__":
    main()
