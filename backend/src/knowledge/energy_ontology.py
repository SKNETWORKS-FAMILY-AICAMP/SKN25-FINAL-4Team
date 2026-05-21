"""
Honda R&D 에너지 도메인 온톨로지
- rdflib로 OWL 트리플 생성
- RAG Agent 지식베이스용 문장 변환
- pyvis 시각화
- EMS 구조 온톨로지(ems.ttl) 병합 — 81개 미터 메타데이터, 설비 그룹, redundancy pair
"""

from rdflib import Graph, Namespace, RDF, OWL, RDFS, Literal, URIRef
from rdflib.namespace import XSD
from pathlib import Path

ENERGY = Namespace("http://honda-energy.skn.kr/ontology#")
EMS_NS = Namespace("https://nousresearch.local/ems/ontology#")
BASE_DIR = Path(__file__).parent


def build_graph() -> Graph:
    g = Graph()
    g.bind("energy", ENERGY)
    g.bind("owl", OWL)

    # ── 클래스 정의 ──────────────────────────────────────────────

    classes = {
        "EnergySource":    "에너지 공급원 (그리드, PV, CHP)",
        "EnergySystem":    "건물 에너지 시스템 (전기, 냉방, 난방)",
        "Meter":           "에너지 계측기 (전력계, 열량계)",
        "BuildingZone":    "건물 구역 (Honda R&D Offenbach 내 구역)",
        "KPI":             "핵심 성과 지표",
        "AnomalyType":     "이상 유형",
        "WeatherFactor":   "날씨 영향 요인",
    }
    for name, label in classes.items():
        uri = ENERGY[name]
        g.add((uri, RDF.type, OWL.Class))
        g.add((uri, RDFS.label, Literal(label, lang="ko")))

    # ── 서브클래스 ────────────────────────────────────────────────

    subclasses = {
        "GridConnection":   ("EnergySource", "외부 계통 전력 — 독일 공공 전력망 (electricity_P.total)"),
        "PVSystem":         ("EnergySource", "태양광 발전 (electricity_P.PV)"),
        "CHPSystem":        ("EnergySource", "열병합 발전 — 전기(electricity_P.CHP)와 열(heating_P.CHP_heat) 동시 생산"),
        "ElectricalSystem": ("EnergySystem", "전기 시스템 (electricity_P / electricity_W)"),
        "HeatingSystem":    ("EnergySystem", "난방 시스템 (heating_P / heating_W)"),
        "CoolingSystem":    ("EnergySystem", "냉방 시스템 (cooling_P / cooling_W)"),
        # 미터 타입
        "CoolingMeter":     ("Meter", "냉방 계측기 (URN 접두 K — Kühlung). H1·H2·V 구역에 설치"),
        "ElectricalMeter":  ("Meter", "전기 계측기 (URN 접두 Z·ZE). 각 구역 전력 소비 측정"),
        "ThermalMeter":     ("Meter", "열량 계측기 (URN 접두 W — Wärme). H1.W11·W12만 존재. H2.T.Z* 는 전기 미터(ABB-B24)이므로 혼동 주의"),
    }
    for name, (parent, label) in subclasses.items():
        uri = ENERGY[name]
        g.add((uri, RDF.type, OWL.Class))
        g.add((uri, RDFS.subClassOf, ENERGY[parent]))
        g.add((uri, RDFS.label, Literal(label, lang="ko")))

    # ── 건물 구역 인스턴스 (H1~H4, V, WeatherStation) ────────────

    zones = {
        "ZoneH1": (
            "H1 배기가스 시험실",
            "Emission Lab. 차량 배기가스 시험(롤러 시험대·테스트 챔버), HVAC 테스트 벤치. "
            "냉방(K11~K16: CM1·CM2·CM3 및 서버실 O1), 열량(W11·W12: CHP·보일러 총 열량), "
            "전기 29개 미터. PV(H1.Z310: Emission Lab 지붕). "
            "CHP 생산 미터(H1.ZE20). 시설 내 에너지 소비 최대 구역."
        ),
        "ZoneH2": (
            "H2 워크숍·서버실",
            "Workshop & Server Room. 자동차 부품 워크숍, CIS·EU 서버실, Robolab. "
            "냉방(H2.K21: 사무실 HVAC), "
            "배전(H2.T.Z30: 사무동 B2 전체, H2.T.Z31: HVAC 50/51 환기, H2.T.Z32: 로비, "
            "H2.T.Z33: 디자인 스튜디오 급전, H2.T.Z34: 워크숍 급전) — T.Z* 미터는 모두 ABB-B24 전기 미터임. "
            "전기(Z61·Z62·Z63: CIS 서버 냉각·전원, Z64·Z65: EU 서버 전원, "
            "Z66·Z67: 로컬 냉각 스플릿 AC, Z68·Z69·Z70: 환기 시스템 17/16/18, ZE74: Robolab), "
            "PV(H2.Z311: 사무동 옥상, 그룹 3&4의 57.8% ≈ 317kWp, 평균 -33.9kW). "
            "ZE64·ZE65·ZE66·ZE67은 PV가 아닌 서버/냉각 백업 미터(2023년 교정법). "
            "열량 미터 없음 — H2에는 W-계열(난방) 미터가 설치되지 않음."
        ),
        "ZoneH3": (
            "H3 디자인 스튜디오",
            "Design Studio. 자동차 디자인 스튜디오, 주행 시뮬레이터(일반·제어·HVAC), 서버실 O4. "
            "배전(Z40·ZE40: 배전 1, Z41·ZE41: 배전 4 — 각각 주/백업 2023년 교정법 쌍), "
            "서버(Z43·ZE43: O4 냉각 1, Z44·ZE44: O4 냉각 2, Z46: O4 전원, Z71: O4 전원 추가), "
            "환기(Z42: 디자인 스튜디오), 냉각(Z45: 디자인 스튜디오 냉각), "
            "시뮬레이터(Z47: 일반, Z48: 제어, Z49: HVAC), "
            "PV(H3.Z312: 디자인 스튜디오 지붕, 그룹 3&4 23.4% + 그룹 5&6 ≈ 193kWp, 평균 -20.6kW). "
            "ZE40·ZE41·ZE43·ZE44는 PV가 아닌 배전/서버 백업 미터(2023년 교정법). "
            "열량·냉수 미터 없음 — 전기만 계측."
        ),
        "ZoneH4": (
            "H4 사무동 B4",
            "Office Building B4. 사무 공간. "
            "배전(H4.Z50·ZE50: 사무동 B4 배전 3, H4.Z51·ZE51: 사무동 B4 배전 4) 4개 미터. "
            "ZE50·ZE51은 PV가 아닌 배전 백업 미터(2023년 교정법). "
            "소규모 구역 — 열량·냉수 미터 없음."
        ),
        "ZoneV": (
            "V 부지 공통",
            "Site-wide. 외부 계통 연결 변압기(V.Z81·V.Z82: 주차장 변압기 1·2, 20kV→230V 2권선), "
            "PV 주차장(V.Z84: 그룹 1&2 ≈ 136kWp, 평균 -14.6kW; V.ZE84: 2023년 교정법 백업 미터), "
            "중앙 냉각기 총합(V.K21: CM1+CM2+CM3 열량, 평균 54kW, Tvl=6.5°C). "
            "시설 전체 PV 총 용량 749kWp, 연평균 자급률 6년평균 39.6%(2022년 46.9%)."
        ),
        "WeatherStationZone": (
            "기상 관측소",
            "Rooftop Weather Station. Lufft WS501-UMB. "
            "시설 최고점(옥상) 설치. 일사량(Igm, W/m²)·외기온(Ta, °C) 측정. "
            "위치: 50.0848°N, 8.8401°E. PV 발전량 예측·냉방 부하 분석에 활용."
        ),
    }
    for name, (label, comment) in zones.items():
        uri = ENERGY[name]
        g.add((uri, RDF.type, ENERGY.BuildingZone))
        g.add((uri, RDFS.label, Literal(label, lang="ko")))
        g.add((uri, RDFS.comment, Literal(comment, lang="ko")))

    # ── Object Properties ────────────────────────────────────────

    obj_props = [
        ("produces",      "에너지를 생산한다"),
        ("consumes",      "에너지를 소비한다"),
        ("affects",       "영향을 준다"),
        ("derivesKPI",    "KPI를 산출한다"),
        ("measuredBy",    "계측기로 측정된다"),
        ("locatedIn",     "구역에 위치한다"),
        ("aggregatesZone","구역 데이터를 집계한다"),
    ]
    for name, label in obj_props:
        uri = ENERGY[name]
        g.add((uri, RDF.type, OWL.ObjectProperty))
        g.add((uri, RDFS.label, Literal(label, lang="ko")))

    # ── 핵심 관계 트리플 ─────────────────────────────────────────

    relations = [
        ("CHPSystem",     "produces", "ElectricalSystem"),
        ("CHPSystem",     "produces", "HeatingSystem"),
        ("PVSystem",      "produces", "ElectricalSystem"),
        ("GridConnection","produces", "ElectricalSystem"),
        ("CoolingSystem", "consumes", "ElectricalSystem"),
    ]
    for s, p, o in relations:
        g.add((ENERGY[s], ENERGY[p], ENERGY[o]))

    # ── 구역 → 에너지 시스템 집계 관계 ──────────────────────────

    # 구역이 소비/측정하는 에너지 유형 (논문 Table2·3·Fig.1 기반)
    zone_consumes = [
        ("ZoneH1", "ElectricalSystem"),   # 시험실 전기
        ("ZoneH1", "CoolingSystem"),      # CM1·CM2·CM3·서버실 냉방
        ("ZoneH1", "HeatingSystem"),      # CHP·보일러 열량
        ("ZoneH2", "ElectricalSystem"),   # 서버·워크숍·환기 전기
        ("ZoneH2", "CoolingSystem"),      # H2.K21: 사무실 HVAC 냉방
        ("ZoneH3", "ElectricalSystem"),   # 시뮬레이터·서버 전기 (냉난방 미터 없음)
        ("ZoneH4", "ElectricalSystem"),   # 사무동 B4 전기 (냉난방 미터 없음)
        ("ZoneV",  "ElectricalSystem"),   # 주 변압기 전기 인입
    ]
    for zone, system in zone_consumes:
        g.add((ENERGY[zone], ENERGY.consumes, ENERGY[system]))

    # ── 미터 타입 → 구역 위치 관계 ───────────────────────────────

    meter_locations = [
        ("CoolingMeter",  "ZoneH1"),
        ("CoolingMeter",  "ZoneH2"),
        ("CoolingMeter",  "ZoneV"),
        ("ElectricalMeter", "ZoneH1"),
        ("ElectricalMeter", "ZoneH2"),
        ("ElectricalMeter", "ZoneH3"),
        ("ElectricalMeter", "ZoneH4"),
        ("ElectricalMeter", "ZoneV"),
        ("ThermalMeter",  "ZoneH1"),   # H1.W11·W12만 — H2에는 열량 미터 없음
    ]
    for meter, zone in meter_locations:
        g.add((ENERGY[meter], ENERGY.locatedIn, ENERGY[zone]))

    # ── 미터 → 에너지 시스템 측정 관계 ──────────────────────────

    g.add((ENERGY.CoolingMeter,    ENERGY.measuredBy, ENERGY.CoolingSystem))
    g.add((ENERGY.ElectricalMeter, ENERGY.measuredBy, ENERGY.ElectricalSystem))
    g.add((ENERGY.ThermalMeter,    ENERGY.measuredBy, ENERGY.HeatingSystem))

    # 기상관측소 → 날씨 요인 (affects로 통일 — measuredBy 대신)
    g.add((ENERGY.WeatherStationZone, ENERGY.affects, ENERGY.SolarIrradiance))
    g.add((ENERGY.WeatherStationZone, ENERGY.affects, ENERGY.AmbientTemp))

    # ── KPI 개념 ────────────────────────────────────────────────

    kpis = {
        "SelfSufficiency": {
            "label": "자급률",
            "comment": "(PV + CHP) / total_consumption. 이 시설 6년평균 39.6%(2022년 46.9%). 높을수록 그리드 의존도 낮음.",
            "formula": "(PV + CHP) / (grid + PV + CHP)",
            "from": ["PVSystem", "CHPSystem", "GridConnection"],
        },
        "COP": {
            "label": "성능계수 (COP)",
            "comment": "cooling_P.total / cooling_P.cool_elec. 중앙값 2.06. cool_elec=0 시 0나누기 방어 필수.",
            "formula": "cooling_output / cooling_elec",
            "from": ["CoolingSystem"],
        },
        "GridDependency": {
            "label": "그리드 의존도",
            "comment": "grid / total_consumption. 자급률의 반대 개념.",
            "formula": "grid / (grid + PV + CHP)",
            "from": ["GridConnection"],
        },
    }
    for name, info in kpis.items():
        uri = ENERGY[name]
        g.add((uri, RDF.type, ENERGY.KPI))
        g.add((uri, RDFS.label, Literal(info["label"], lang="ko")))
        g.add((uri, RDFS.comment, Literal(info["comment"], lang="ko")))
        g.add((uri, ENERGY["formula"], Literal(info["formula"])))
        for src in info["from"]:
            # 방향: 에너지시스템/공급원 → KPI (좌→우 흐름 유지)
            g.add((ENERGY[src], ENERGY.derivesKPI, uri))

    # ── 이상 유형 ────────────────────────────────────────────────

    anomalies = {
        "PowerSpike":       ("전력 급등 이상", "electricity_P.total 급증. 그리드 과부하 위험."),
        "COPDrop":          ("COP 급락 이상", "냉방 효율 저하. 냉각기 과부하 또는 냉매 부족 의심."),
        "NightConsumption": ("야간 이상 소비", "운영 시간 외 전력 소비. 설비 미차단 또는 침입 의심."),
        "CHPOutage":        ("CHP 정지 이상", "CHP 전기·열 동시 0. 자급률 급감, 그리드 의존도 증가."),
        "PVNightNonZero":   ("PV 야간 비정상", "야간 PV > 0은 센서 오류. 정상 야간 NaN과 구별 필요."),
    }
    for name, (label, comment) in anomalies.items():
        uri = ENERGY[name]
        g.add((uri, RDF.type, ENERGY.AnomalyType))
        g.add((uri, RDFS.label, Literal(label, lang="ko")))
        g.add((uri, RDFS.comment, Literal(comment, lang="ko")))

    # ── 이상 → 영향 관계 ─────────────────────────────────────────

    anomaly_affects = [
        ("COPDrop",          "CoolingSystem"),
        ("COPDrop",          "COP"),
        ("CHPOutage",        "SelfSufficiency"),
        ("CHPOutage",        "GridDependency"),
        ("PowerSpike",       "ElectricalSystem"),
        ("NightConsumption", "ElectricalSystem"),
        ("PVNightNonZero",   "PVSystem"),
    ]
    for anomaly, target in anomaly_affects:
        g.add((ENERGY[anomaly], ENERGY.affects, ENERGY[target]))

    # ── 날씨 요인 ────────────────────────────────────────────────

    weather = {
        "SolarIrradiance": ("일사량 (Igm)", "PV 발전량에 직접 영향. weather.csv.gz의 Igm 컬럼 (W/m²)."),
        "AmbientTemp":     ("외기온 (Ta)",  "냉방 부하 및 COP에 영향. weather.csv.gz의 Ta 컬럼 (°C)."),
    }
    for name, (label, comment) in weather.items():
        uri = ENERGY[name]
        g.add((uri, RDF.type, ENERGY.WeatherFactor))
        g.add((uri, RDFS.label, Literal(label, lang="ko")))
        g.add((uri, RDFS.comment, Literal(comment, lang="ko")))

    g.add((ENERGY.SolarIrradiance, ENERGY.affects, ENERGY.PVSystem))
    g.add((ENERGY.AmbientTemp,     ENERGY.affects, ENERGY.CoolingSystem))
    g.add((ENERGY.AmbientTemp,     ENERGY.affects, ENERGY.HeatingSystem))

    # EMS 구조 온톨로지 병합 (ems.ttl — 81개 미터 인스턴스, 설비 그룹, redundancy pair)
    ttl_path = BASE_DIR / "ems.ttl"
    if ttl_path.exists():
        g.parse(str(ttl_path), format="turtle")

    return g


def save_owl(g: Graph, path: str = None) -> Path:
    out = Path(path) if path else BASE_DIR / "energy_ontology.owl"
    g.serialize(str(out), format="turtle")
    print(f"OWL 저장: {out}")
    return out


def to_rag_sentences(g: Graph) -> list[str]:
    """온톨로지 트리플을 RAG용 자연어 문장으로 변환."""
    sentences = []
    for s, p, o in g:
        s_label = g.value(s, RDFS.label) or s.split("#")[-1] if isinstance(s, URIRef) else str(s)
        o_label = g.value(o, RDFS.label) or o.split("#")[-1] if isinstance(o, URIRef) else str(o)

        if p == RDFS.comment:
            sentences.append(f"[지식] {s_label}: {o_label}")
        elif p == ENERGY.produces:
            sentences.append(f"[관계] {s_label}은 {o_label}을 생산한다.")
        elif p == ENERGY.consumes:
            sentences.append(f"[관계] {s_label}은 {o_label}을 소비한다.")
        elif p == ENERGY.affects:
            sentences.append(f"[관계] {s_label}은 {o_label}에 영향을 준다.")
        elif p == ENERGY.derivesKPI:
            sentences.append(f"[계산] {s_label}으로부터 {o_label}이 산출된다.")
        elif p == ENERGY["formula"]:
            sentences.append(f"[공식] {s_label} 계산식: {o_label}")

    # EMS 구조 온톨로지에서 미터·그룹·redundancy 문장 추출
    sentences.extend(_ems_rag_sentences(g))

    return [s for s in sentences if s.strip()]


def _ems_rag_sentences(g: Graph) -> list[str]:
    """EMS 구조 온톨로지(ems.ttl)에서 미터 메타데이터 RAG 문장 추출."""
    EMS = EMS_NS
    sentences = []

    for meter_uri in g.subjects(RDF.type, EMS.Meter):
        urn = g.value(meter_uri, EMS.meterUrn)
        if not urn:
            continue
        urn = str(urn)
        domain   = g.value(meter_uri, EMS.meterDomain)
        role     = g.value(meter_uri, EMS.meterRoleCode)
        group    = g.value(meter_uri, EMS.equipmentGroupCode)
        building = g.value(meter_uri, EMS.buildingCode)
        priority = g.value(meter_uri, EMS.anomalyPriority)
        eq_name  = g.value(meter_uri, EMS.equipmentName)
        sign     = g.value(meter_uri, EMS.signConvention)

        parts = [f"[미터] {urn}"]
        if domain:
            parts.append(f"도메인={domain}")
        if role:
            parts.append(f"역할={role}")
        if group:
            parts.append(f"그룹={group}")
        if building:
            parts.append(f"건물={building}")
        if eq_name and str(eq_name):
            parts.append(f"설비명={eq_name}")
        if priority:
            parts.append(f"이상탐지우선순위={priority}")
        if sign:
            parts.append(f"부호규약={sign}")
        sentences.append(", ".join(parts))

    for pair_uri in g.subjects(RDF.type, EMS.RedundancyPair):
        label   = g.value(pair_uri, RDFS.label)
        group   = g.value(pair_uri, EMS.equipmentGroupCode)
        eq_name = g.value(pair_uri, EMS.equipmentName)
        if label:
            s = f"[중복계량] {label}"
            if group:
                s += f", 그룹={group}"
            if eq_name:
                s += f", 설비={eq_name}"
            sentences.append(s)

    for group_uri in g.subjects(RDF.type, EMS.EquipmentGroup):
        code     = g.value(group_uri, EMS.equipmentGroupCode)
        count    = g.value(group_uri, EMS.meterCount)
        priority = g.value(group_uri, EMS.anomalyPriority)
        if code:
            s = f"[설비그룹] {code}"
            if count:
                s += f", 미터수={count}"
            if priority:
                s += f", 이상탐지우선순위={priority}"
            sentences.append(s)

    return sentences


def visualize(g: Graph, output_html: str = None):
    """pyvis로 인터랙티브 그래프 HTML 생성."""
    try:
        from pyvis.network import Network
    except ImportError:
        print("pyvis 미설치. 실행: pip install pyvis")
        return

    out = Path(output_html) if output_html else BASE_DIR / "energy_ontology_graph.html"

    BG = "#0d1117"
    FG = "#e6edf3"

    # 노드 색상
    color_map = {
        "EnergySource":  {"background": "#ff6b6b", "border": "#ff4444"},
        "EnergySystem":  {"background": "#4ecdc4", "border": "#2ab7ae"},
        "KPI":           {"background": "#ffe66d", "border": "#e6c933"},
        "AnomalyType":   {"background": "#c084fc", "border": "#a855f7"},
        "WeatherFactor": {"background": "#6bcb77", "border": "#4caf57"},
        "BuildingZone":  {"background": "#fb923c", "border": "#ea6b0a"},
    }
    default_color = {"background": "#6b7280", "border": "#9ca3af"}

    # 수동 x,y 좌표 — 4열 배치
    # 열0(-750): 건물 구역 / 열1(-250): 에너지 공급원·날씨 / 열2(250): 에너지 시스템 / 열3(750): KPI·이상
    NODE_POS = {
        # 건물 구역 (열0)
        "ZoneH1":             (-750, -350),
        "ZoneH2":             (-750, -180),
        "ZoneH3":             (-750,   -10),
        "ZoneH4":             (-750,  160),
        "ZoneV":              (-750,  300),
        "WeatherStationZone": (-750,  460),
        # 에너지 공급원 (열1 상단)
        "GridConnection":     (-250, -380),
        "PVSystem":           (-250, -210),
        "CHPSystem":          (-250,  -40),
        # 날씨 요인 (열1 하단)
        "SolarIrradiance":    (-250,  280),
        "AmbientTemp":        (-250,  440),
        # 에너지 시스템 (열2)
        "ElectricalSystem":   ( 250, -230),
        "HeatingSystem":      ( 250,   50),
        "CoolingSystem":      ( 250,  310),
        # KPI (열3 상단)
        "SelfSufficiency":    ( 750, -420),
        "GridDependency":     ( 750, -260),
        "COP":                ( 750, -100),
        # 이상 유형 (열3 하단)
        "PowerSpike":         ( 750,  120),
        "COPDrop":            ( 750,  270),
        "CHPOutage":          ( 750,  420),
        "NightConsumption":   ( 750,  570),
        "PVNightNonZero":     ( 750,  720),
    }

    # 표시할 관계 — measuredBy 제거 (계측기 노드 불필요)
    vis_props = {
        ENERGY.produces:   ("생산",    "#ff6b6b", 2.5),
        ENERGY.consumes:   ("소비",    "#ffa94d", 2.5),
        ENERGY.affects:    ("영향",    "#f472b6", 2.0),
        ENERGY.derivesKPI: ("KPI산출", "#60a5fa", 2.0),
    }

    net = Network(height="100vh", width="100%", bgcolor=BG, font_color=FG,
                  directed=True, notebook=False)
    net.set_options(f"""
    {{
      "physics": {{ "enabled": false }},
      "edges": {{
        "arrows": {{"to": {{"enabled": true, "scaleFactor": 0.9}}}},
        "smooth": {{"type": "curvedCW", "roundness": 0.15}},
        "font": {{"size": 11, "color": "{FG}", "align": "middle",
                  "strokeWidth": 3, "strokeColor": "{BG}"}}
      }},
      "nodes": {{
        "font": {{"size": 13, "color": "#0d1117", "bold": true,
                  "strokeWidth": 2, "strokeColor": "rgba(0,0,0,0.25)"}},
        "margin": 12,
        "widthConstraint": {{"minimum": 130, "maximum": 165}},
        "shadow": {{"enabled": true, "color": "rgba(0,0,0,0.4)", "size": 6, "x": 2, "y": 2}}
      }}
    }}
    """)

    def get_type(uri):
        for klass in color_map:
            if (uri, RDF.type, ENERGY[klass]) in g:
                return klass
            parent = g.value(uri, RDFS.subClassOf)
            if parent and (parent, RDF.type, ENERGY[klass]) in g:
                return klass
        return None

    def get_color(uri):
        return color_map.get(get_type(uri), default_color)

    added_nodes = set()

    def add_node(uri):
        if str(uri) in added_nodes or not isinstance(uri, URIRef):
            return
        local = str(uri).split("#")[-1]
        # 계측기 노드(Meter 서브클래스)는 시각화에서 제외
        if get_type(uri) == "Meter":
            return
        if local not in NODE_POS:
            return  # 좌표 없는 노드는 그리지 않음
        label_lit = g.value(uri, RDFS.label)
        label   = str(label_lit).split("@")[0] if label_lit else local
        color   = get_color(uri)
        tooltip = str(g.value(uri, RDFS.comment) or "")
        x, y    = NODE_POS[local]
        net.add_node(str(uri), label=label, color=color, size=28,
                     x=x, y=y, physics=False, title=tooltip, shape="ellipse")
        added_nodes.add(str(uri))

    for prop, (edge_label, edge_color, width) in vis_props.items():
        for s, _, o in g.triples((None, prop, None)):
            if not isinstance(s, URIRef) or not isinstance(o, URIRef):
                continue
            add_node(s)
            add_node(o)
            if str(s) not in added_nodes or str(o) not in added_nodes:
                continue
            net.add_edge(str(s), str(o), label=edge_label,
                         color={"color": edge_color, "highlight": edge_color, "opacity": 0.9},
                         width=width)

    net.save_graph(str(out))

    html = out.read_text(encoding="utf-8")

    # lib/bindings/utils.js 로컬 참조 → 인라인으로 교체 (lib/ 디렉터리 불필요)
    UTILS_JS = """<script>
function neighbourhoodHighlight(params) {
  allNodes = nodes.get({ returnType: "Object" });
  if (params.nodes.length > 0) {
    highlightActive = true;
    var i, j, selectedNode = params.nodes[0], degrees = 2;
    for (let nodeId in allNodes) {
      allNodes[nodeId].color = "rgba(200,200,200,0.5)";
      if (allNodes[nodeId].hiddenLabel === undefined) {
        allNodes[nodeId].hiddenLabel = allNodes[nodeId].label;
        allNodes[nodeId].label = undefined;
      }
    }
    var connectedNodes = network.getConnectedNodes(selectedNode), allConnectedNodes = [];
    for (i = 1; i < degrees; i++)
      for (j = 0; j < connectedNodes.length; j++)
        allConnectedNodes = allConnectedNodes.concat(network.getConnectedNodes(connectedNodes[j]));
    for (i = 0; i < allConnectedNodes.length; i++) {
      allNodes[allConnectedNodes[i]].color = "rgba(150,150,150,0.75)";
      if (allNodes[allConnectedNodes[i]].hiddenLabel !== undefined) {
        allNodes[allConnectedNodes[i]].label = allNodes[allConnectedNodes[i]].hiddenLabel;
        allNodes[allConnectedNodes[i]].hiddenLabel = undefined;
      }
    }
    for (i = 0; i < connectedNodes.length; i++) {
      allNodes[connectedNodes[i]].color = nodeColors[connectedNodes[i]];
      if (allNodes[connectedNodes[i]].hiddenLabel !== undefined) {
        allNodes[connectedNodes[i]].label = allNodes[connectedNodes[i]].hiddenLabel;
        allNodes[connectedNodes[i]].hiddenLabel = undefined;
      }
    }
    allNodes[selectedNode].color = nodeColors[selectedNode];
    if (allNodes[selectedNode].hiddenLabel !== undefined) {
      allNodes[selectedNode].label = allNodes[selectedNode].hiddenLabel;
      allNodes[selectedNode].hiddenLabel = undefined;
    }
  } else if (highlightActive === true) {
    for (let nodeId in allNodes) {
      allNodes[nodeId].color = nodeColors[nodeId];
      if (allNodes[nodeId].hiddenLabel !== undefined) {
        allNodes[nodeId].label = allNodes[nodeId].hiddenLabel;
        allNodes[nodeId].hiddenLabel = undefined;
      }
    }
    highlightActive = false;
  }
  var updateArray = [];
  for (let nodeId in allNodes)
    if (allNodes.hasOwnProperty(nodeId)) updateArray.push(allNodes[nodeId]);
  nodes.update(updateArray);
}
function filterHighlight(params) {
  allNodes = nodes.get({ returnType: "Object" });
  if (params.nodes.length > 0) {
    filterActive = true;
    for (let nodeId in allNodes) {
      allNodes[nodeId].hidden = true;
      if (allNodes[nodeId].savedLabel === undefined) {
        allNodes[nodeId].savedLabel = allNodes[nodeId].label;
        allNodes[nodeId].label = undefined;
      }
    }
    for (let i = 0; i < params.nodes.length; i++) {
      allNodes[params.nodes[i]].hidden = false;
      if (allNodes[params.nodes[i]].savedLabel !== undefined) {
        allNodes[params.nodes[i]].label = allNodes[params.nodes[i]].savedLabel;
        allNodes[params.nodes[i]].savedLabel = undefined;
      }
    }
  } else if (filterActive === true) {
    for (let nodeId in allNodes) {
      allNodes[nodeId].hidden = false;
      if (allNodes[nodeId].savedLabel !== undefined) {
        allNodes[nodeId].label = allNodes[nodeId].savedLabel;
        allNodes[nodeId].savedLabel = undefined;
      }
    }
    filterActive = false;
  }
  var updateArray = [];
  for (let nodeId in allNodes)
    if (allNodes.hasOwnProperty(nodeId)) updateArray.push(allNodes[nodeId]);
  nodes.update(updateArray);
}
function selectNode(nodes) { network.selectNodes(nodes); neighbourhoodHighlight({ nodes: nodes }); return nodes; }
function selectNodes(nodes) { network.selectNodes(nodes); filterHighlight({nodes: nodes}); return nodes; }
function highlightFilter(filter) {
  let selectedNodes = [], selectedProp = filter['property'];
  if (filter['item'] === 'node') {
    let allNodes = nodes.get({ returnType: "Object" });
    for (let nodeId in allNodes)
      if (allNodes[nodeId][selectedProp] && filter['value'].includes((allNodes[nodeId][selectedProp]).toString()))
        selectedNodes.push(nodeId);
  } else if (filter['item'] === 'edge') {
    let allEdges = edges.get({returnType: 'object'});
    for (let edge in allEdges)
      if (allEdges[edge][selectedProp] && filter['value'].includes((allEdges[edge][selectedProp]).toString())) {
        selectedNodes.push(allEdges[edge]['from']); selectedNodes.push(allEdges[edge]['to']);
      }
  }
  selectNodes(selectedNodes);
}
    </script>"""
    html = html.replace('<script src="lib/bindings/utils.js"></script>', UTILS_JS)

    # 제목 + 범례 + 전체 페이지 스타일 주입
    overlay = """
<style>
  body { margin: 0; background: #0d1117; font-family: 'Segoe UI', sans-serif; }
  #title-bar {
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    background: rgba(13,17,23,0.92); backdrop-filter: blur(8px);
    border-bottom: 1px solid #30363d;
    padding: 12px 24px; display: flex; align-items: center; gap: 16px;
  }
  #title-bar h1 { margin: 0; font-size: 17px; color: #e6edf3; font-weight: 600; }
  #title-bar span { font-size: 13px; color: #8b949e; }
  #legend {
    position: fixed; bottom: 24px; left: 24px; z-index: 100;
    background: rgba(22,27,34,0.95); border: 1px solid #30363d;
    border-radius: 10px; padding: 14px 18px;
  }
  #legend h3 { margin: 0 0 10px; font-size: 12px; color: #8b949e;
               text-transform: uppercase; letter-spacing: 0.08em; }
  .legend-row { display: flex; align-items: center; gap: 8px;
                margin-bottom: 6px; font-size: 13px; color: #e6edf3; }
  .dot { width: 13px; height: 13px; border-radius: 50%; flex-shrink: 0; }
  #edge-legend { margin-top: 12px; border-top: 1px solid #30363d; padding-top: 10px; }
  .edge-row { display: flex; align-items: center; gap: 8px;
              margin-bottom: 5px; font-size: 12px; color: #c9d1d9; }
  .edge-line { width: 24px; height: 3px; border-radius: 2px; flex-shrink: 0; }
  #hint { position: fixed; bottom: 24px; right: 24px; z-index: 100;
          background: rgba(22,27,34,0.85); border: 1px solid #30363d;
          border-radius: 8px; padding: 10px 14px;
          font-size: 12px; color: #8b949e; line-height: 1.6; }
</style>

<div id="title-bar">
  <h1>Honda R&amp;D 에너지 온톨로지</h1>
  <span>에너지 시스템 개념 및 관계 구조</span>
</div>

<div id="legend">
  <h3>노드 유형</h3>
  <div class="legend-row"><div class="dot" style="background:#ff6b6b"></div>에너지 공급원</div>
  <div class="legend-row"><div class="dot" style="background:#4ecdc4"></div>에너지 시스템</div>
  <div class="legend-row"><div class="dot" style="background:#fb923c"></div>건물 구역 (H1~H4, V)</div>
  <div class="legend-row"><div class="dot" style="background:#ffe66d"></div>KPI 지표</div>
  <div class="legend-row"><div class="dot" style="background:#c084fc"></div>이상 유형</div>
  <div class="legend-row"><div class="dot" style="background:#6bcb77"></div>날씨 요인</div>
  <div id="edge-legend">
    <h3 style="margin:0 0 8px">관계 유형</h3>
    <div class="edge-row"><div class="edge-line" style="background:#ff6b6b"></div>생산</div>
    <div class="edge-row"><div class="edge-line" style="background:#ffa94d"></div>소비</div>
    <div class="edge-row"><div class="edge-line" style="background:#f472b6"></div>영향</div>
    <div class="edge-row"><div class="edge-line" style="background:#60a5fa"></div>KPI 산출</div>
  </div>
</div>

<div id="hint">노드 위에 마우스를 올리면 설명이 표시됩니다<br>드래그로 노드 이동 가능</div>
"""
    html = html.replace("<body>", "<body>" + overlay)

    out.write_text(html, encoding="utf-8")
    print(f"시각화 저장: {out}")
    return out


def visualize_hierarchy(output_html: str = None) -> Path:
    """
    Mermaid.js 기반 ERD 스타일 계층 구조 HTML 생성.
    공장 → 구역 → 에너지 시스템 → 공급원/KPI/이상 순으로 내려간다.
    """
    out = Path(output_html) if output_html else BASE_DIR / "energy_hierarchy.html"

    BG   = "#0d1117"
    FG   = "#e6edf3"
    MUTED = "#8b949e"

    diagram = """
graph TD
    classDef factory  fill:#1e3a5f,stroke:#3b82f6,color:#e6edf3,font-weight:bold
    classDef zone     fill:#c2410c,stroke:#fb923c,color:#fff,font-weight:bold
    classDef system   fill:#0e7490,stroke:#4ecdc4,color:#fff
    classDef source   fill:#991b1b,stroke:#ff6b6b,color:#fff
    classDef weather  fill:#166534,stroke:#6bcb77,color:#fff
    classDef kpi      fill:#854d0e,stroke:#ffe66d,color:#fff,font-weight:bold
    classDef anomaly  fill:#581c87,stroke:#c084fc,color:#fff

    FACTORY["🏭 Honda R&amp;D Europe<br/>오펜바흐, 독일<br/>6개 구역 · 81개 미터 · 2017~2024"]:::factory

    FACTORY --> H1["H1 · 배기가스 시험실<br/>차량 배기가스 시험 · HVAC 테스트<br/>전기 29개 · 냉방 4개 · 열량 2개 미터"]:::zone
    FACTORY --> H2["H2 · 워크숍 · 서버실<br/>자동차 부품 워크숍 · CIS/EU 서버<br/>전기 · 냉방 · 열량 미터"]:::zone
    FACTORY --> H3["H3 · 디자인 스튜디오<br/>자동차 디자인 · 주행 시뮬레이터<br/>전기 미터 전용"]:::zone
    FACTORY --> H4["H4 · 사무동 B4<br/>사무 공간<br/>전기 4개 미터"]:::zone
    FACTORY --> V["V · 주 배전반 · 주차장<br/>20kV→230V 변압기 4기<br/>시설 전체 그리드 인입점"]:::zone
    FACTORY --> WS["🌤 기상 관측소<br/>Lufft WS501-UMB<br/>옥상 설치 · 50.08°N 8.84°E"]:::zone

    H1 --> ELEC["⚡ 전기 시스템"]:::system
    H1 --> COOL["❄️ 냉방 시스템"]:::system
    H1 --> HEAT["🔥 난방 시스템"]:::system
    H2 --> ELEC
    H2 --> COOL
    H2 --> HEAT
    H3 --> ELEC
    H4 --> ELEC
    V  --> ELEC

    GRID["🔌 외부 계통 전력<br/>독일 공공 전력망"]:::source --> ELEC
    PV["☀️ 태양광 PV<br/>H1·H2·H3·V 지붕<br/>4개 미터 합산"]:::source   --> ELEC
    CHP["⚙️ 열병합 CHP<br/>전기 + 열 동시 생산<br/>H1.ZE20 · H1.W12"]:::source --> ELEC
    CHP --> HEAT

    WS --> IGM["🌞 일사량 Igm<br/>W/m²"]:::weather
    WS --> TA["🌡️ 외기온 Ta<br/>°C"]:::weather
    IGM -->|PV 발전 직접 영향| PV
    TA  -->|냉방 부하 영향| COOL
    TA  -->|난방 부하 영향| HEAT

    GRID --> SS["📊 자급률<br/>Self-Sufficiency<br/>6년평균 39.6%(2022년 46.9%)<br/>(PV+CHP)÷Total"]:::kpi
    PV   --> SS
    CHP  --> SS
    GRID --> GD["📊 그리드 의존도<br/>Grid Dependency<br/>Grid÷Total"]:::kpi
    COOL --> COP_KPI["📊 COP<br/>성능계수<br/>중앙값 2.06<br/>Cooling÷Elec"]:::kpi

    COOL --> COPD["⚠️ COPDrop<br/>냉방 효율 저하<br/>냉각기 과부하·냉매 부족"]:::anomaly
    CHP  --> CHPO["⚠️ CHPOutage<br/>CHP 정지<br/>자급률 급감"]:::anomaly
    ELEC --> PS["⚠️ PowerSpike<br/>전력 급등<br/>그리드 과부하 위험"]:::anomaly
    ELEC --> NC["⚠️ NightConsumption<br/>야간 이상 소비<br/>설비 미차단·침입 의심"]:::anomaly
    PV   --> PVN["⚠️ PVNightNonZero<br/>야간 PV 비정상<br/>센서 오류"]:::anomaly
"""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Honda R&D 에너지 온톨로지 — 계층 구조</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: {BG}; font-family: 'Segoe UI', sans-serif; }}
  #title-bar {{
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    background: rgba(13,17,23,0.95); backdrop-filter: blur(8px);
    border-bottom: 1px solid #30363d;
    padding: 12px 24px; display: flex; align-items: center; gap: 16px;
  }}
  #title-bar h1 {{ font-size: 17px; color: {FG}; font-weight: 600; }}
  #title-bar span {{ font-size: 13px; color: {MUTED}; }}
  #diagram-wrap {{
    margin-top: 56px;
    padding: 32px 24px 80px;
    display: flex;
    justify-content: center;
  }}
  .mermaid {{ width: 100%; max-width: 1400px; }}
  #legend {{
    position: fixed; bottom: 24px; left: 24px; z-index: 100;
    background: rgba(22,27,34,0.95); border: 1px solid #30363d;
    border-radius: 10px; padding: 14px 18px;
  }}
  #legend h3 {{
    margin: 0 0 10px; font-size: 11px; color: {MUTED};
    text-transform: uppercase; letter-spacing: 0.08em;
  }}
  .legend-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12px; color: {FG}; }}
  .dot {{ width: 12px; height: 12px; border-radius: 3px; flex-shrink: 0; }}
</style>
</head>
<body>

<div id="title-bar">
  <h1>Honda R&amp;D 에너지 온톨로지 — 계층 구조</h1>
  <span>공장 위치 → 구역 → 에너지 시스템 → KPI · 이상 유형</span>
</div>

<div id="diagram-wrap">
  <div class="mermaid">
{diagram}
  </div>
</div>

<div id="legend">
  <h3>노드 유형</h3>
  <div class="legend-row"><div class="dot" style="background:#1e3a5f;border:1px solid #3b82f6"></div>시설 (공장)</div>
  <div class="legend-row"><div class="dot" style="background:#c2410c;border:1px solid #fb923c"></div>건물 구역</div>
  <div class="legend-row"><div class="dot" style="background:#0e7490;border:1px solid #4ecdc4"></div>에너지 시스템</div>
  <div class="legend-row"><div class="dot" style="background:#991b1b;border:1px solid #ff6b6b"></div>에너지 공급원</div>
  <div class="legend-row"><div class="dot" style="background:#166534;border:1px solid #6bcb77"></div>날씨 요인</div>
  <div class="legend-row"><div class="dot" style="background:#854d0e;border:1px solid #ffe66d"></div>KPI 지표</div>
  <div class="legend-row"><div class="dot" style="background:#581c87;border:1px solid #c084fc"></div>이상 유형</div>
</div>

<script>
  mermaid.initialize({{
    startOnLoad: true,
    theme: 'dark',
    themeVariables: {{
      primaryColor:        '#1e3a5f',
      primaryTextColor:    '{FG}',
      primaryBorderColor:  '#3b82f6',
      lineColor:           '#4b5563',
      secondaryColor:      '#0e7490',
      tertiaryColor:       '#161b22',
      background:          '{BG}',
      mainBkg:             '#161b22',
      nodeBorder:          '#30363d',
      clusterBkg:          '#161b22',
      titleColor:          '{FG}',
      edgeLabelBackground: '#161b22',
      fontFamily:          'Segoe UI, sans-serif',
    }},
    flowchart: {{ htmlLabels: true, curve: 'basis', padding: 20, nodeSpacing: 40, rankSpacing: 70 }},
  }});
</script>
</body>
</html>"""

    out.write_text(html, encoding="utf-8")
    print(f"계층 구조 시각화 저장: {out}")
    return out


if __name__ == "__main__":
    g = build_graph()
    save_owl(g)

    sentences = to_rag_sentences(g)
    print(f"\nRAG 문장 {len(sentences)}개 생성:")
    for s in sentences[:10]:
        print(" ", s)

    visualize(g)
    visualize_hierarchy()
