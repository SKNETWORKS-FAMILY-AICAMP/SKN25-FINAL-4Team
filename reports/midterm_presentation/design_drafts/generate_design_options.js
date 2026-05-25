const pptxgen = require('./.node/node_modules/pptxgenjs');
const fs = require('fs');
const path = require('path');

const OUT = __dirname;
const W = 13.333;
const H = 7.5;
const FONT = 'Noto Sans CJK KR';
const MONO = 'Noto Sans Mono CJK KR';

const agenda = ['필요성', '개요', '데이터', 'EDA', '모델링/FEMS', '진행현황', '목표'];
const pressures = ['CBAM', 'K-ETS', '탄소중립', '에너지 비용'];
const pipeline = ['EMS 데이터', '예측', '이상탐지', '피크관리', 'LLM 운영지원'];
const title = '고해상도 EMS 기반\nFEMS 데이터 인사이트';
const subtitle = '제조업 에너지 운영 판단을 위한 분석·예측·이상탐지 검증';
const meta = 'SKN25 최종 4팀 · 중간발표';

const themes = [
  {
    id: '01_industrial_control_dark',
    name: '산업 관제 다크',
    bg: '071318', panel: '0D232A', panel2: '102E35', text: 'F4FBFA', muted: '9EC4C3',
    accent: '20E0C2', accent2: '6FE7FF', warn: 'F6C76B', line: '1F4A52'
  },
  {
    id: '02_executive_light_grid',
    name: '경영진 라이트 그리드',
    bg: 'F5F7F3', panel: 'FFFFFF', panel2: 'E8EFEA', text: '17211D', muted: '65736E',
    accent: '188A63', accent2: '0F6F8C', warn: 'B8792D', line: 'D8E1DC'
  },
  {
    id: '03_thermal_report_warm',
    name: '열지도 리포트 웜',
    bg: 'F7EFE3', panel: 'FFF9EF', panel2: 'E8D5BC', text: '251A12', muted: '7A6452',
    accent: 'B85042', accent2: 'D9844A', warn: '6E2F24', line: 'DEC7AA'
  },
  {
    id: '04_blueprint_technical',
    name: '블루프린트 테크니컬',
    bg: '081A33', panel: '0F2D52', panel2: '103B67', text: 'F1F7FF', muted: 'A9C7E8',
    accent: '64D2FF', accent2: '9BE7C7', warn: 'FFD166', line: '244B75'
  },
  {
    id: '05_mono_precision',
    name: '모노 프리시전',
    bg: 'F7F7F4', panel: 'FFFFFF', panel2: 'EDEDE8', text: '151515', muted: '6C6C66',
    accent: 'FF6B35', accent2: '2E7D6B', warn: 'A53F2B', line: 'D7D7D0'
  }
];

function newDeck(theme, titleSuffix = '') {
  const pptx = new pptxgen();
  pptx.layout = 'LAYOUT_WIDE';
  pptx.author = 'Hermes Agent';
  pptx.company = 'SKN25 FINAL 4Team';
  pptx.subject = `중간발표 디자인 시안 ${theme.name}`;
  pptx.title = `중간발표 디자인 시안 ${theme.name}${titleSuffix}`;
  pptx.lang = 'ko-KR';
  pptx.theme = {
    headFontFace: FONT,
    bodyFontFace: FONT,
    lang: 'ko-KR'
  };
  return pptx;
}

function base(slide, t) {
  slide.background = { color: t.bg };
  // subtle grid
  for (let x = 0; x <= W; x += 0.5) {
    slide.addShape('line', { x, y: 0, w: 0, h: H, line: { color: t.line, transparency: 72, width: 0.35 } });
  }
  for (let y = 0; y <= H; y += 0.5) {
    slide.addShape('line', { x: 0, y, w: W, h: 0, line: { color: t.line, transparency: 78, width: 0.35 } });
  }
}

function tx(slide, text, opt) {
  slide.addText(text, Object.assign({ fontFace: FONT, margin: 0, fit: 'shrink' }, opt));
}

function rect(slide, x, y, w, h, fill, line, transparency = 0, radius = false) {
  slide.addShape(radius ? 'roundRect' : 'rect', {
    x, y, w, h,
    fill: { color: fill, transparency },
    line: line ? { color: line, width: 0.8, transparency: 20 } : { color: fill, transparency: 100 },
    radius: radius ? 0.12 : undefined
  });
}

function line(slide, x, y, w, h, color, width = 1, trans = 0, dash) {
  slide.addShape('line', { x, y, w, h, line: { color, width, transparency: trans, dash } });
}

function dot(slide, x, y, r, color, trans = 0) {
  slide.addShape('ellipse', { x, y, w: r, h: r, fill: { color, transparency: trans }, line: { color, transparency: 100 } });
}

function factory(slide, t, x, y, scale = 1, dark = true) {
  const c = dark ? t.accent : t.text;
  rect(slide, x, y + 1.18*scale, 3.4*scale, 0.72*scale, t.panel2, t.line, 0, false);
  rect(slide, x + 0.18*scale, y + 0.86*scale, 0.52*scale, 0.34*scale, t.panel2, t.line, 0, false);
  rect(slide, x + 0.95*scale, y + 0.66*scale, 0.52*scale, 0.54*scale, t.panel2, t.line, 0, false);
  rect(slide, x + 1.72*scale, y + 0.44*scale, 0.52*scale, 0.76*scale, t.panel2, t.line, 0, false);
  rect(slide, x + 2.68*scale, y + 0.18*scale, 0.38*scale, 1.02*scale, t.panel2, t.line, 0, false);
  for (let i=0;i<5;i++) rect(slide, x + (0.35+i*0.55)*scale, y + 1.37*scale, 0.23*scale, 0.18*scale, c, c, 15, false);
  line(slide, x + 3.1*scale, y + 0.36*scale, 1.0*scale, -0.42*scale, t.accent, 2, 10);
  line(slide, x + 3.1*scale, y + 0.72*scale, 1.25*scale, -0.16*scale, t.accent2, 1.4, 25);
  dot(slide, x + 4.1*scale, y - 0.11*scale, 0.12*scale, t.accent, 0);
  dot(slide, x + 4.32*scale, y + 0.49*scale, 0.10*scale, t.accent2, 0);
}

function nodeNetwork(slide, t, x, y, scale = 1) {
  const pts = [[0,0.5],[0.7,0.05],[1.4,0.62],[2.1,0.2],[2.75,0.85],[3.45,0.42]];
  for (let i=0;i<pts.length-1;i++) line(slide, x+pts[i][0]*scale, y+pts[i][1]*scale, (pts[i+1][0]-pts[i][0])*scale, (pts[i+1][1]-pts[i][1])*scale, t.accent, 1.2, 35);
  pts.forEach((p, i)=> {
    dot(slide, x+p[0]*scale-0.045, y+p[1]*scale-0.045, 0.09*scale, i%2 ? t.accent2 : t.accent, 0);
    dot(slide, x+p[0]*scale-0.095, y+p[1]*scale-0.095, 0.19*scale, i%2 ? t.accent2 : t.accent, 75);
  });
}

function smallLabel(slide, t, text, x, y) {
  rect(slide, x, y, 1.55, 0.34, t.panel, t.line, 6, true);
  tx(slide, text, { x: x+0.12, y: y+0.08, w: 1.3, h: 0.16, color: t.muted, fontSize: 7.5, bold: true, breakLine: false, align: 'center' });
}

function slideCover(pptx, t, opt) {
  const s = pptx.addSlide(); base(s, t);
  if (opt === 1) {
    rect(s, 0, 0, 13.333, 7.5, '02090C', null, 0);
    rect(s, 0.75, 0.72, 11.85, 6.05, t.panel, t.line, 0, true);
    rect(s, 0.75, 0.72, 0.22, 6.05, t.accent, null, 0, false);
    factory(s, t, 8.35, 3.55, 0.83);
    nodeNetwork(s, t, 8.25, 2.25, 0.78);
    tx(s, '중간발표', { x: 1.2, y: 1.13, w: 2, h: 0.3, color: t.accent, fontSize: 13, bold: true, charSpacing: 4 });
    tx(s, title, { x: 1.15, y: 2.02, w: 6.8, h: 1.55, color: t.text, fontSize: 35, bold: true, breakLine: true, fit: 'shrink' });
    tx(s, subtitle, { x: 1.18, y: 4.02, w: 6.9, h: 0.42, color: t.muted, fontSize: 14 });
    tx(s, meta, { x: 1.18, y: 5.82, w: 3.8, h: 0.25, color: t.muted, fontSize: 10.5 });
    smallLabel(s, t, '산업 데이터 분석 중심', 9.15, 5.72);
  } else if (opt === 2) {
    rect(s, 0.48, 0.52, 12.37, 6.42, t.panel, t.line, 0, false);
    rect(s, 7.85, 0.52, 5.0, 6.42, t.panel2, t.line, 0, false);
    factory(s, t, 8.35, 2.72, 1.03, false);
    nodeNetwork(s, t, 8.15, 1.62, 0.92);
    tx(s, '제조업 에너지 운영', { x: 0.9, y: 1.12, w: 3.8, h: 0.32, color: t.accent, fontSize: 12, bold: true, charSpacing: 2 });
    tx(s, title, { x: 0.9, y: 2.05, w: 6.1, h: 1.5, color: t.text, fontSize: 34, bold: true });
    tx(s, '공개 EMS 데이터를 실증용 기준으로 삼아\n피크·설비·품질 인사이트를 검증합니다.', { x: 0.92, y: 4.2, w: 5.85, h: 0.72, color: t.muted, fontSize: 14, breakLine: true });
    tx(s, meta, { x: 0.92, y: 6.1, w: 3.6, h: 0.22, color: t.muted, fontSize: 10 });
  } else if (opt === 3) {
    rect(s, 0, 0, 13.333, 7.5, t.bg, null, 0);
    for (let i=0;i<9;i++) rect(s, 7.35+i*0.38, 0.9+i*0.34, 2.6, 0.32, i%2?t.accent:t.accent2, null, 58-i*4, false);
    rect(s, 0.8, 0.8, 11.75, 5.95, t.panel, t.line, 0, true);
    tx(s, '중간발표 시안', { x: 1.18, y: 1.08, w: 2.6, h: 0.3, color: t.warn, fontSize: 12, bold: true });
    tx(s, title, { x: 1.16, y: 2.0, w: 6.7, h: 1.55, color: t.text, fontSize: 34, bold: true });
    tx(s, subtitle, { x: 1.18, y: 4.05, w: 5.9, h: 0.44, color: t.muted, fontSize: 14 });
    factory(s, t, 8.32, 3.55, 0.8, false);
    tx(s, meta, { x: 1.18, y: 5.95, w: 3.8, h: 0.25, color: t.muted, fontSize: 10 });
  } else if (opt === 4) {
    rect(s, 0.55, 0.5, 12.25, 6.5, t.panel, t.line, 5, false);
    rect(s, 0.88, 0.83, 11.6, 5.84, t.bg, t.accent, 0, false);
    tx(s, '설계 도면 형식', { x: 1.05, y: 1.05, w: 3, h: 0.25, color: t.accent, fontSize: 11, bold: true, charSpacing: 3 });
    tx(s, title, { x: 1.05, y: 2.02, w: 6.9, h: 1.55, color: t.text, fontSize: 33, bold: true });
    factory(s, t, 8.48, 3.35, 0.82);
    nodeNetwork(s, t, 8.3, 2.05, 0.76);
    tx(s, subtitle, { x: 1.08, y: 4.45, w: 6.2, h: 0.4, color: t.muted, fontSize: 13 });
    tx(s, meta, { x: 1.08, y: 6.22, w: 3.8, h: 0.25, color: t.muted, fontSize: 10 });
  } else {
    rect(s, 0.78, 0.68, 11.78, 6.08, t.panel, t.line, 0, false);
    tx(s, '중간발표', { x: 1.12, y: 1.1, w: 2.0, h: 0.3, color: t.accent, fontSize: 11, bold: true, charSpacing: 5 });
    tx(s, title, { x: 1.08, y: 2.0, w: 6.7, h: 1.56, color: t.text, fontSize: 36, bold: true });
    tx(s, subtitle, { x: 1.1, y: 4.12, w: 6.5, h: 0.38, color: t.muted, fontSize: 13.5 });
    rect(s, 8.4, 1.46, 3.25, 3.25, t.panel2, t.line, 0, false);
    factory(s, t, 8.82, 2.72, 0.72, false);
    nodeNetwork(s, t, 8.72, 1.95, 0.68);
    tx(s, meta, { x: 1.1, y: 6.02, w: 3.8, h: 0.25, color: t.muted, fontSize: 10 });
  }
}

function slideAgenda(pptx, t, opt) {
  const s = pptx.addSlide(); base(s, t);
  tx(s, '발표 흐름', { x: 0.75, y: 0.55, w: 3.2, h: 0.4, color: t.text, fontSize: 25, bold: true });
  tx(s, '필요성에서 시작해 데이터와 분석 방향을 거쳐 최종 목표로 이어지는 구조입니다.', { x: 0.78, y: 1.05, w: 6.8, h: 0.3, color: t.muted, fontSize: 11 });
  if (opt === 1 || opt === 4) {
    const startX = 0.9, y = 3.35;
    agenda.forEach((a, i) => {
      const x = startX + i*1.72;
      dot(s, x, y, 0.34, i < 3 ? t.accent : t.panel2, 0);
      tx(s, String(i+1), { x: x, y: y+0.08, w: 0.34, h: 0.12, color: i<3?t.bg:t.accent, fontSize: 8, bold: true, align: 'center' });
      tx(s, a, { x: x-0.42, y: y+0.55, w: 1.25, h: 0.42, color: t.text, fontSize: 12, bold: i<3 });
      if (i < agenda.length-1) line(s, x+0.38, y+0.17, 1.15, 0, i<2 ? t.accent : t.line, 2.2, i<2?0:20);
    });
    rect(s, 0.88, 5.35, 11.6, 0.68, t.panel, t.line, 0, true);
    tx(s, '이번 시안 범위: 1쪽 표지 · 2쪽 목차 · 3쪽 프로젝트 필요성', { x: 1.12, y: 5.58, w: 9.2, h: 0.2, color: t.muted, fontSize: 11.5, bold: true });
  } else if (opt === 2) {
    agenda.forEach((a, i) => {
      const y = 1.72 + i*0.62;
      rect(s, 1.05, y, 7.45, 0.42, i<3?t.panel2:t.panel, t.line, 0, true);
      tx(s, `${String(i+1).padStart(2,'0')}`, { x: 1.25, y: y+0.12, w: 0.45, h: 0.1, color: i<3?t.accent:t.muted, fontSize: 8, bold: true });
      tx(s, a, { x: 1.88, y: y+0.09, w: 4.0, h: 0.16, color: t.text, fontSize: 12.5, bold: i<3 });
    });
    rect(s, 9.05, 1.75, 2.8, 3.95, t.panel2, t.line, 0, true);
    tx(s, '초반 3장 역할', { x: 9.35, y: 2.15, w: 2.0, h: 0.25, color: t.text, fontSize: 16, bold: true });
    tx(s, '문제 정의\n발표 구조 안내\n필요성 설득', { x: 9.38, y: 2.82, w: 2.0, h: 1.25, color: t.muted, fontSize: 15, breakLine: true, bold: true });
  } else if (opt === 3) {
    const boxes = [
      ['01', '필요성', '정책·운영 압력'], ['02', '개요', '분석 흐름'], ['03', '데이터', '출처·범위'],
      ['04', 'EDA', '기준 수립'], ['05', '모델링', '예측·탐지'], ['06', '목표', '검증 결과']
    ];
    boxes.forEach((b, i) => {
      const x = 0.9 + (i%3)*4.0, y = 1.82 + Math.floor(i/3)*1.55;
      rect(s, x, y, 3.35, 1.04, i===0?t.accent:i===1?t.accent2:t.panel, t.line, i<2?0:0, true);
      tx(s, b[0], { x:x+0.18, y:y+0.18, w:0.5, h:0.2, color:i<2?t.bg:t.warn, fontSize:10, bold:true });
      tx(s, b[1], { x:x+0.82, y:y+0.18, w:1.6, h:0.25, color:i<2?t.bg:t.text, fontSize:16, bold:true });
      tx(s, b[2], { x:x+0.82, y:y+0.58, w:2.2, h:0.18, color:i<2?t.bg:t.muted, fontSize:9.5 });
    });
    tx(s, '시안에는 1~3쪽만 제작했습니다.', { x: 0.92, y: 5.45, w: 5.0, h: 0.25, color: t.muted, fontSize: 11, bold: true });
  } else {
    agenda.forEach((a, i) => {
      const x = 0.95 + (i%4)*3.05, y = 1.85 + Math.floor(i/4)*1.5;
      rect(s, x, y, 2.55, 0.92, i<3?t.panel2:t.panel, t.line, 0, false);
      tx(s, `${i+1}`, { x:x+0.18, y:y+0.18, w:0.3, h:0.12, color:t.accent, fontSize:8, bold:true });
      tx(s, a, { x:x+0.62, y:y+0.22, w:1.55, h:0.17, color:t.text, fontSize:12.2, bold:i<3 });
    });
    rect(s, 0.95, 5.55, 11.3, 0.48, t.accent, null, 0, false);
    tx(s, '초반 3장은 발표의 문제 정의와 방향성을 결정하는 구간입니다.', { x: 1.18, y: 5.71, w: 8.0, h: 0.13, color: t.bg, fontSize: 10.5, bold: true });
  }
}

function slideNeed(pptx, t, opt) {
  const s = pptx.addSlide(); base(s, t);
  tx(s, '프로젝트 필요성', { x: 0.72, y: 0.52, w: 4.2, h: 0.42, color: t.text, fontSize: 25, bold: true });
  tx(s, '외부 압력과 내부 EMS 데이터를 운영 판단으로 연결하는 체계가 필요합니다.', { x: 0.75, y: 1.06, w: 7.4, h: 0.3, color: t.muted, fontSize: 11 });

  if (opt === 1 || opt === 4) {
    tx(s, '외부 압력', { x: 0.88, y: 1.72, w: 2.0, h: 0.24, color: t.accent, fontSize: 14, bold: true });
    pressures.forEach((p, i) => {
      const y = 2.18 + i*0.82;
      rect(s, 0.9, y, 3.0, 0.54, t.panel, t.line, 0, true);
      dot(s, 1.12, y+0.15, 0.22, i%2?t.accent2:t.warn, 0);
      tx(s, p, { x: 1.48, y: y+0.17, w: 1.9, h: 0.12, color: t.text, fontSize: 11.5, bold: true });
    });
    tx(s, 'EMS 데이터 활용 흐름', { x: 5.0, y: 1.72, w: 3.0, h: 0.24, color: t.accent, fontSize: 14, bold: true });
    pipeline.forEach((p, i) => {
      const x = 5.05 + (i%3)*2.35, y = 2.18 + Math.floor(i/3)*1.25;
      rect(s, x, y, 1.9, 0.68, i===0?t.accent:t.panel, t.line, 0, true);
      tx(s, p, { x:x+0.12, y:y+0.25, w:1.65, h:0.12, color:i===0?t.bg:t.text, fontSize:9.3, bold:true, align:'center' });
      if (i < pipeline.length-1) line(s, x+1.92, y+0.34, 0.39, 0, t.accent, 1.5, 15);
    });
    rect(s, 5.05, 5.45, 6.75, 0.72, t.panel2, t.line, 0, true);
    tx(s, '핵심: 저장된 계측값을 피크 위험·설비 이상 후보·운영 의사결정 지원으로 확장', { x:5.28, y:5.7, w:6.2, h:0.15, color:t.text, fontSize:10.5, bold:true });
  } else if (opt === 2) {
    const cols = [
      ['외부 압력', pressures.join('\n')],
      ['내부 자산', '1분·15분·1시간\n계측 데이터 축적'],
      ['운영 판단', '피크 위험\n이상 후보\n비효율 탐지']
    ];
    cols.forEach((c, i) => {
      const x = 0.9 + i*4.05;
      rect(s, x, 1.85, 3.45, 3.4, i===1?t.panel2:t.panel, t.line, 0, true);
      tx(s, c[0], { x:x+0.32, y:2.16, w:2.4, h:0.25, color:t.accent, fontSize:15, bold:true });
      tx(s, c[1], { x:x+0.32, y:2.9, w:2.65, h:1.55, color:t.text, fontSize:16, bold:true, breakLine:true });
    });
    rect(s, 1.05, 5.8, 10.9, 0.44, t.text, null, 0, false);
    tx(s, '검증 방향: 공개 독일 EMS 데이터를 실증용 기준으로 활용해 한국 제조업 FEMS 적용 가능성을 확인', { x:1.28, y:5.94, w:9.8, h:0.12, color:t.bg, fontSize:9.8, bold:true });
  } else if (opt === 3) {
    // warm matrix
    for (let i=0;i<4;i++) {
      rect(s, 0.9+i*2.2, 1.85, 1.8, 1.2, i%2?t.accent2:t.accent, t.line, 0, false);
      tx(s, pressures[i], { x:1.02+i*2.2, y:2.28, w:1.55, h:0.15, color:t.bg, fontSize:11, bold:true, align:'center' });
    }
    tx(s, '이미 축적되는 고해상도 계측 데이터', { x: 0.95, y: 3.65, w: 5.0, h:0.3, color:t.text, fontSize:18, bold:true });
    tx(s, '전력 · 냉난방 · PV/CHP · 기상 · 계량기별 부하', { x: 0.98, y: 4.15, w: 5.8, h:0.25, color:t.muted, fontSize:12, bold:true });
    rect(s, 7.3, 3.48, 4.95, 1.58, t.panel, t.line, 0, true);
    tx(s, '운영 판단으로 연결', { x:7.62, y:3.82, w:2.4, h:0.25, color:t.warn, fontSize:16, bold:true });
    tx(s, '예측 · 이상탐지 · 피크관리 · 질의/요약', { x:7.65, y:4.38, w:3.5, h:0.2, color:t.text, fontSize:12.2, bold:true });
  } else {
    const left = ['정책 압력', '비용 변동', '탄소 관리', '피크 위험'];
    left.forEach((v,i)=> {
      const y=1.9+i*0.72;
      tx(s, v, { x:1.0, y:y+0.11, w:2.0, h:0.13, color:t.text, fontSize:12.5, bold:true });
      line(s, 3.15, y+0.22, 2.2, 0, t.line, 1.2, 0);
      dot(s, 5.55, y+0.13, 0.18, t.accent, 0);
    });
    rect(s, 6.05, 1.65, 5.75, 3.7, t.panel, t.line, 0, false);
    tx(s, 'EMS 데이터에서 도출할 판단', { x:6.38, y:2.02, w:3.5, h:0.24, color:t.accent, fontSize:15, bold:true });
    tx(s, '피크 위험 예측\n설비 이상 후보 분리\n에너지 비효율 탐지\nLLM 기반 질의·요약', { x:6.42, y:2.72, w:3.8, h:1.55, color:t.text, fontSize:17, bold:true, breakLine:true });
    tx(s, '현장 확인이 필요한 후보를 우선순위화하는 관점으로 표현했습니다.', { x:6.42, y:4.75, w:4.5, h:0.18, color:t.muted, fontSize:9.5 });
  }
}

async function buildTheme(theme, opt) {
  const deck = newDeck(theme);
  slideCover(deck, theme, opt);
  slideAgenda(deck, theme, opt);
  slideNeed(deck, theme, opt);
  const file = path.join(OUT, `${theme.id}.pptx`);
  await deck.writeFile({ fileName: file });
  return file;
}

async function main() {
  const files = [];
  for (let i=0;i<themes.length;i++) files.push(await buildTheme(themes[i], i+1));

  const combo = newDeck(themes[0], ' 전체보기');
  for (let i=0;i<themes.length;i++) {
    slideCover(combo, themes[i], i+1);
    slideAgenda(combo, themes[i], i+1);
    slideNeed(combo, themes[i], i+1);
  }
  const comboFile = path.join(OUT, 'all_5_design_options_preview.pptx');
  await combo.writeFile({ fileName: comboFile });

  const readme = `# 중간발표 디자인 시안\n\n최신 slide_script.md를 기준으로 1~3쪽만 제작한 디자인 시안입니다.\n\n## 파일\n\n${themes.map((t,i)=>`${i+1}. ${t.id}.pptx — ${t.name}`).join('\n')}\n6. all_5_design_options_preview.pptx — 다섯 시안을 한 파일에서 빠르게 비교\n\n## 반영 범위\n\n- 1쪽 표지: 공장/산업시설, 에너지 데이터 흐름, EMS/FEMS/AI 키워드\n- 2쪽 목차: 필요성 → 개요 → 데이터 → EDA → 모델링/FEMS → 진행현황 → 목표 흐름\n- 3쪽 프로젝트 필요성: CBAM, K-ETS, 탄소중립, 에너지 비용 압력과 EMS 데이터 활용 파이프라인\n\n## 주의\n\n발표자 실명과 팀 공식 명칭은 스크립트에 없어 임시로 “SKN25 최종 4팀 · 중간발표”로 표기했습니다.\n`;
  fs.writeFileSync(path.join(OUT, 'README.md'), readme, 'utf8');
  console.log(JSON.stringify({ files, comboFile, readme: path.join(OUT, 'README.md') }, null, 2));
}

main().catch(err => { console.error(err); process.exit(1); });
