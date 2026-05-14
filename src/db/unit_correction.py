"""
DB full_measurement_definition 단위/명칭 오류 보정 테이블

오류 항목:
  - Ua: DB는 m/s로 기재 → 실제는 % (상대습도)
  - H:  DB는 % 로 기재  → 실제는 kJ/kg (비엔탈피)
  - Sc: DB 명칭          → 실제 공식명 Sac (풍속 m/s)
  - Igc: DB 명칭         → 실제 공식명 Iga
  - rho: DB는 g/cm³     → 실제는 kg/m³ (1000배 차이)
  - V:   DB는 L          → 실제는 m³   (1000배 차이)
  - qv:  DB는 L/h        → 실제는 m³/h (1000배 차이)

DB 수정 완료 후 이 파일 제거 예정.
"""

UNIT_CORRECTION: dict[str, str] = {
    "Ua":  "%",       # 상대습도. DB는 m/s로 잘못 기재
    "H":   "kJ/kg",   # 비엔탈피. DB는 %로 잘못 기재
    "Sc":  "m/s",     # 풍속. 공식명 Sac
    "Igc": "W/m²",    # 일사량. 공식명 Iga
    "rho": "kg/m³",   # DB는 g/cm³로 잘못 기재
    "V":   "m³",      # DB는 L로 잘못 기재
    "qv":  "m³/h",    # DB는 L/h로 잘못 기재
}

NAME_CORRECTION: dict[str, str] = {
    "Sc":  "Sac",
    "Igc": "Iga",
}


def get_unit(measurement: str, db_unit: str) -> str:
    """DB 단위 오류 보정. 보정 대상 아니면 DB 값 그대로 반환."""
    return UNIT_CORRECTION.get(measurement, db_unit)


def get_name(measurement: str) -> str:
    """DB 명칭 오류 보정. 보정 대상 아니면 원래 명칭 반환."""
    return NAME_CORRECTION.get(measurement, measurement)
