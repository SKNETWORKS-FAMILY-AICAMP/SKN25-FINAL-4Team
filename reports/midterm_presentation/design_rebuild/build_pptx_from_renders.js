const pptxgen = require('../design_drafts/.node/node_modules/pptxgenjs');
const path = require('path');

const base = __dirname;
const pptx = new pptxgen();
pptx.layout = 'LAYOUT_WIDE';
pptx.author = 'Hermes Agent';
pptx.company = 'SKN25 FINAL 4Team';
pptx.subject = '중간발표 고품질 시안';
pptx.title = '고해상도 EMS 기반 FEMS 데이터 인사이트 시안';
pptx.lang = 'ko-KR';
pptx.theme = { headFontFace: 'Noto Sans CJK KR', bodyFontFace: 'Noto Sans CJK KR', lang: 'ko-KR' };
pptx.defineLayout({ name: 'CUSTOM_WIDE', width: 13.333333, height: 7.5 });
pptx.layout = 'CUSTOM_WIDE';

for (let i = 1; i <= 3; i++) {
  const slide = pptx.addSlide();
  slide.background = { color: i === 2 ? 'EEE6D8' : '111413' };
  slide.addImage({ path: path.join(base, 'rendered', `slide_${String(i).padStart(2,'0')}.png`), x: 0, y: 0, w: 13.333333, h: 7.5 });
}

pptx.writeFile({ fileName: path.join(base, 'fems_premium_3slides_visual_checked.pptx') });
