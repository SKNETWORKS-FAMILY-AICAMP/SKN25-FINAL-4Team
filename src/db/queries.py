"""
cr_measurement_1h EAV → 피벗 SQL 쿼리 모음

EAV 구조:
  ts | meter_urn | measurement | value

피벗 후:
  ts | P | W | PF | ... (계량기별 measurement 컬럼화)

계량기 분류:
  전기 계량기: 71개 (.Zxx, .ZExx 패턴), measurement 22개
  열량계:       9개 (.Kxx, .Wxx 패턴),  measurement 7개
  기상관측소:   1개 (WeatherStation.Weather), measurement 10개
"""

PIVOT_ELECTRIC_SQL = """
SELECT
    ts,
    meter_urn,
    MAX(CASE WHEN measurement = 'P'  THEN value END) AS "P",
    MAX(CASE WHEN measurement = 'W'  THEN value END) AS "W",
    MAX(CASE WHEN measurement = 'PF' THEN value END) AS "PF",
    MAX(CASE WHEN measurement = 'Q'  THEN value END) AS "Q",
    MAX(CASE WHEN measurement = 'I1' THEN value END) AS "I1",
    MAX(CASE WHEN measurement = 'I2' THEN value END) AS "I2",
    MAX(CASE WHEN measurement = 'I3' THEN value END) AS "I3",
    MAX(CASE WHEN measurement = 'U1' THEN value END) AS "U1",
    MAX(CASE WHEN measurement = 'U2' THEN value END) AS "U2",
    MAX(CASE WHEN measurement = 'U3' THEN value END) AS "U3",
    MAX(CASE WHEN measurement = 'f'  THEN value END) AS "f"
FROM ems.cr_measurement_1h
WHERE meter_urn = :meter_urn
  AND ts BETWEEN :start_ts AND :end_ts
GROUP BY ts, meter_urn
ORDER BY ts
"""

PIVOT_THERMAL_SQL = """
SELECT
    ts,
    meter_urn,
    MAX(CASE WHEN measurement = 'P'     THEN value END) AS "P",
    MAX(CASE WHEN measurement = 'W'     THEN value END) AS "W",
    MAX(CASE WHEN measurement = 'Tvl'   THEN value END) AS "Tvl",
    MAX(CASE WHEN measurement = 'Trl'   THEN value END) AS "Trl",
    MAX(CASE WHEN measurement = 'Tdiff' THEN value END) AS "Tdiff",
    MAX(CASE WHEN measurement = 'qv'    THEN value END) AS "qv",
    MAX(CASE WHEN measurement = 'V'     THEN value END) AS "V"
FROM ems.cr_measurement_1h
WHERE meter_urn = :meter_urn
  AND ts BETWEEN :start_ts AND :end_ts
GROUP BY ts, meter_urn
ORDER BY ts
"""

PIVOT_WEATHER_SQL = """
SELECT
    ts,
    meter_urn,
    MAX(CASE WHEN measurement = 'Ta'  THEN value END) AS "Ta",
    MAX(CASE WHEN measurement = 'Igm' THEN value END) AS "Igm",
    MAX(CASE WHEN measurement = 'Igc' THEN value END) AS "Igc",
    MAX(CASE WHEN measurement = 'Ua'  THEN value END) AS "Ua",
    MAX(CASE WHEN measurement = 'H'   THEN value END) AS "H",
    MAX(CASE WHEN measurement = 'Dc'  THEN value END) AS "Dc",
    MAX(CASE WHEN measurement = 'Sc'  THEN value END) AS "Sc",
    MAX(CASE WHEN measurement = 'Dp'  THEN value END) AS "Dp",
    MAX(CASE WHEN measurement = 'rho' THEN value END) AS "rho",
    MAX(CASE WHEN measurement = 'Ah'  THEN value END) AS "Ah"
FROM ems.cr_measurement_1h
WHERE meter_urn = 'WeatherStation.Weather'
  AND ts BETWEEN :start_ts AND :end_ts
GROUP BY ts, meter_urn
ORDER BY ts
"""

METER_LIST_SQL = """
SELECT DISTINCT meter_urn
FROM ems.cr_measurement_1h
ORDER BY meter_urn
"""
