-- R2 P-Max live holdout policy window extension.
--
-- STATUS: APPLY PACKET. Non-destructive policy metadata update; no data row
-- delete/move, no canonical write, no promotion.
--
-- Purpose:
--   The R2 live holdout year is 2023 KST. Existing P-Max live-observed policies
--   for V.Z81/V.Z82/H2.Z351/H2.Z361 P/U1/PF start at 2023-12-01 KST, which
--   causes Jan~Nov 2023 replay events to land as policy_miss. Extend those
--   policy effective_from values to 2023-01-01 KST before full-year replay.
--
-- Safety:
--   - Updates only policies with paper_policy_ref =
--     'pmax_live_observed_materialization_20260611'.
--   - Restricts to the 4 P-Max live 2023 meters and P/U1/PF.
--   - Does not touch SMOKE policies or canonical eligibility.

\set ON_ERROR_STOP on

DO $$
BEGIN
    IF current_setting('cms.allow_r2_policy_update', true) <> '1' THEN
        RAISE EXCEPTION 'r2_extend_pmax_policy_2023.sql is gated: SET LOCAL cms.allow_r2_policy_update = 1 in an approved admin transaction';
    END IF;
END
$$;

WITH target AS (
    SELECT policy_id
    FROM live.measurement_policy
    WHERE meter_urn IN ('V.Z81', 'V.Z82', 'H2.Z351', 'H2.Z361')
      AND measurement IN ('P', 'U1', 'PF')
      AND paper_policy_ref = 'pmax_live_observed_materialization_20260611'
      AND effective_from > timestamptz '2023-01-01 00:00:00+09'
), updated AS (
    UPDATE live.measurement_policy AS p
    SET effective_from = timestamptz '2023-01-01 00:00:00+09',
        updated_at = now()
    FROM target AS t
    WHERE p.policy_id = t.policy_id
    RETURNING p.policy_id, p.meter_urn, p.measurement, p.effective_from, p.effective_to, p.enabled, p.canonical_eligible
)
SELECT 'r2_policy_updated' AS check_name, count(*) AS updated_count
FROM updated;

SELECT
    'r2_policy_window' AS check_name,
    meter_urn,
    measurement,
    effective_from,
    effective_to,
    enabled,
    canonical_eligible,
    peak_feature_enabled
FROM live.measurement_policy
WHERE meter_urn IN ('V.Z81', 'V.Z82', 'H2.Z351', 'H2.Z361')
  AND measurement IN ('P', 'U1', 'PF')
  AND paper_policy_ref = 'pmax_live_observed_materialization_20260611'
ORDER BY meter_urn, measurement;
