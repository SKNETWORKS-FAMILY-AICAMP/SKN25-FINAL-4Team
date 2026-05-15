-- ==============================================================================
-- EMS Reduced Aggregated View (논문 Table 8 기준)
-- ==============================================================================
-- 이 스크립트는 ems.cr_measurement_15min 및 ems.cr_measurement_1h 테이블을
-- 기반으로 건물 전체 수준의 거시적 에너지 흐름을 보여주는 뷰를 생성합니다.
--
-- Categories:
-- 1. Electricity (total, pv, chp)
-- 2. Heating (total, chp_heat, chp_elec)
-- 3. Cooling (total, cool_elec)
-- 4. Weather (Igm, Ta)
-- ==============================================================================

-- 15분 해상도 뷰
CREATE OR REPLACE VIEW ems.reduced_measurement_15min AS
WITH agg AS (
    -- Electricity: total
    SELECT ts, 'electricity' AS category, 'total' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_15min
    WHERE meter_urn IN ('V.Z81', 'V.Z82', 'H2.Z35', 'H2.Z351', 'H2.Z36', 'H2.Z361')
      AND measurement IN ('P', 'W', 'Q', 'WQ')
    GROUP BY ts, measurement

    UNION ALL
    -- Electricity: PV
    SELECT ts, 'electricity' AS category, 'pv' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_15min
    WHERE meter_urn IN ('V.Z84', 'H1.Z310', 'H2.Z311', 'H3.Z312')
      AND measurement IN ('P', 'W', 'Q', 'WQ')
    GROUP BY ts, measurement

    UNION ALL
    -- Electricity: CHP
    SELECT ts, 'electricity' AS category, 'chp' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_15min
    WHERE meter_urn = 'H1.Z20'
      AND measurement IN ('P', 'W', 'Q', 'WQ')
    GROUP BY ts, measurement

    UNION ALL
    -- Heating: total
    SELECT ts, 'heating' AS category, 'total' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_15min
    WHERE meter_urn = 'H1.W11'
      AND measurement IN ('P', 'W')
    GROUP BY ts, measurement

    UNION ALL
    -- Heating: chp_heat
    SELECT ts, 'heating' AS category, 'chp_heat' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_15min
    WHERE meter_urn = 'H1.W12'
      AND measurement IN ('P', 'W')
    GROUP BY ts, measurement

    UNION ALL
    -- Heating: chp_elec
    SELECT ts, 'heating' AS category, 'chp_elec' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_15min
    WHERE meter_urn = 'H1.Z20'
      AND measurement IN ('P', 'W')
    GROUP BY ts, measurement

    UNION ALL
    -- Cooling: total
    SELECT ts, 'cooling' AS category, 'total' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_15min
    WHERE meter_urn = 'V.K21'
      AND measurement IN ('P', 'W')
    GROUP BY ts, measurement

    UNION ALL
    -- Cooling: cool_elec
    SELECT ts, 'cooling' AS category, 'cool_elec' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_15min
    WHERE meter_urn IN ('H1.Z16', 'H1.Z11', 'H1.Z12', 'H1.Z24', 'H1.Z25')
      AND measurement IN ('P', 'W', 'Q', 'WQ')
    GROUP BY ts, measurement

    UNION ALL
    -- Weather
    SELECT ts, 'weather' AS category, 'weather' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_15min
    WHERE meter_urn = 'WeatherStation.Weather'
      AND measurement IN ('Igm', 'Ta')
    GROUP BY ts, measurement
)
SELECT * FROM agg;

-- 1시간 해상도 뷰
CREATE OR REPLACE VIEW ems.reduced_measurement_1h AS
WITH agg AS (
    -- Electricity: total
    SELECT ts, 'electricity' AS category, 'total' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_1h
    WHERE meter_urn IN ('V.Z81', 'V.Z82', 'H2.Z35', 'H2.Z351', 'H2.Z36', 'H2.Z361')
      AND measurement IN ('P', 'W', 'Q', 'WQ')
    GROUP BY ts, measurement

    UNION ALL
    -- Electricity: PV
    SELECT ts, 'electricity' AS category, 'pv' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_1h
    WHERE meter_urn IN ('V.Z84', 'H1.Z310', 'H2.Z311', 'H3.Z312')
      AND measurement IN ('P', 'W', 'Q', 'WQ')
    GROUP BY ts, measurement

    UNION ALL
    -- Electricity: CHP
    SELECT ts, 'electricity' AS category, 'chp' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_1h
    WHERE meter_urn = 'H1.Z20'
      AND measurement IN ('P', 'W', 'Q', 'WQ')
    GROUP BY ts, measurement

    UNION ALL
    -- Heating: total
    SELECT ts, 'heating' AS category, 'total' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_1h
    WHERE meter_urn = 'H1.W11'
      AND measurement IN ('P', 'W')
    GROUP BY ts, measurement

    UNION ALL
    -- Heating: chp_heat
    SELECT ts, 'heating' AS category, 'chp_heat' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_1h
    WHERE meter_urn = 'H1.W12'
      AND measurement IN ('P', 'W')
    GROUP BY ts, measurement

    UNION ALL
    -- Heating: chp_elec
    SELECT ts, 'heating' AS category, 'chp_elec' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_1h
    WHERE meter_urn = 'H1.Z20'
      AND measurement IN ('P', 'W')
    GROUP BY ts, measurement

    UNION ALL
    -- Cooling: total
    SELECT ts, 'cooling' AS category, 'total' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_1h
    WHERE meter_urn = 'V.K21'
      AND measurement IN ('P', 'W')
    GROUP BY ts, measurement

    UNION ALL
    -- Cooling: cool_elec
    SELECT ts, 'cooling' AS category, 'cool_elec' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_1h
    WHERE meter_urn IN ('H1.Z16', 'H1.Z11', 'H1.Z12', 'H1.Z24', 'H1.Z25')
      AND measurement IN ('P', 'W', 'Q', 'WQ')
    GROUP BY ts, measurement

    UNION ALL
    -- Weather
    SELECT ts, 'weather' AS category, 'weather' AS subcategory, measurement, SUM(value) AS value
    FROM ems.cr_measurement_1h
    WHERE meter_urn = 'WeatherStation.Weather'
      AND measurement IN ('Igm', 'Ta')
    GROUP BY ts, measurement
)
SELECT * FROM agg;
