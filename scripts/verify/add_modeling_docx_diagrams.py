from __future__ import annotations

import hashlib
import struct
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET

ROOT = Path('/home/viowlet/Projects/SKN25-FINAL-4Team')
FINAL_DIR = ROOT / 'reports/modeling_evaluation_docs/final'
DIAGRAM_DIR = FINAL_DIR / 'diagrams'
SOURCE_DIAGRAM_DIR = ROOT / 'docs/specs/diagrams'

W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
WP_NS = 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
A_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
PIC_NS = 'http://schemas.openxmlformats.org/drawingml/2006/picture'
REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'

ET.register_namespace('w', W_NS)
ET.register_namespace('r', R_NS)
ET.register_namespace('wp', WP_NS)
ET.register_namespace('a', A_NS)
ET.register_namespace('pic', PIC_NS)
ET.register_namespace('', REL_NS)

W = f'{{{W_NS}}}'
R = f'{{{R_NS}}}'
WP = f'{{{WP_NS}}}'
A = f'{{{A_NS}}}'
PIC = f'{{{PIC_NS}}}'
REL = f'{{{REL_NS}}}'
CT = f'{{{CT_NS}}}'

TEMPLATES = {
    'ai_system_architecture': Path('/mnt/hgfs/Windows/[모델링 및 평가] AI 시스템 아키텍처 (멀티 에이전트 아키텍처)_양식.docx'),
    'multi_agent_test_report': Path('/mnt/hgfs/Windows/[모델링 및 평가] 멀티 에이전트 테스트 계획 및 결과 보고서_양식.docx'),
    'vector_graph_db_result': Path('/mnt/hgfs/Windows/[모델링 및 평가] 벡터DB_GraphDB 구축 결과서_양식.docx'),
}

# Use the existing canonical project diagrams under docs/specs/diagrams.
# Only PNG renderings are generated for DOCX embedding.
DOCS = {
    'ai_system_architecture': {
        'docx': FINAL_DIR / 'ai_system_architecture.docx',
        'source_svg': SOURCE_DIAGRAM_DIR / 'flow_00_overall_pipeline.svg',
        'png': DIAGRAM_DIR / 'flow_00_overall_pipeline.png',
        'anchor': '1. 에이전트 설계 개요',
        'caption': '그림 1. CMS 전체 처리 구조',
        'note': '계측 입력, MongoDB 원천 영역, 처리 작업자, PostgreSQL 스키마, 서비스 응답, 지식 근거화 경계를 연결한 전체 구조입니다.',
    },
    'multi_agent_test_report': {
        'docx': FINAL_DIR / 'multi_agent_test_report.docx',
        'source_svg': SOURCE_DIAGRAM_DIR / 'sequence_00_overall_pipeline.svg',
        'png': DIAGRAM_DIR / 'sequence_00_overall_pipeline.png',
        'anchor': '1. 테스트 개요 및 환경 설정',
        'caption': '그림 1. 전체 처리 흐름 검증 순서',
        'note': '원천, 데이터 저장소, 품질검증 근거, 승인, 지식 근거화, 작업흐름, FastAPI 앱 사이의 검증 대상 순서입니다.',
    },
    'vector_graph_db_result': {
        'docx': FINAL_DIR / 'vector_graph_db_result.docx',
        'source_svg': SOURCE_DIAGRAM_DIR / 'flow_01_database_pipeline.svg',
        'png': DIAGRAM_DIR / 'flow_01_database_pipeline.png',
        'anchor': '1. 구축 목적 및 적용 범위',
        'caption': '그림 1. 데이터베이스와 지식 근거화 경로',
        'note': 'PostgreSQL, MongoDB, 온톨로지 메타데이터, Graphify 기준 문서 맥락, FastAPI 읽기 전용 경로를 구분한 지식 기반 구조입니다.',
    },
}


def render_png(svg: Path, png: Path) -> None:
    if not svg.exists():
        raise FileNotFoundError(svg)
    png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['rsvg-convert', '-w', '1600', str(svg), '-o', str(png)], check=True)


def png_size(path: Path) -> tuple[int, int]:
    with path.open('rb') as f:
        sig = f.read(24)
    if sig[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError(f'not a PNG: {path}')
    return struct.unpack('>II', sig[16:24])


def rel_id(rel_root: ET.Element) -> str:
    max_id = 0
    for rel in rel_root.findall(f'{REL}Relationship'):
        rid = rel.get('Id', '')
        if rid.startswith('rId') and rid[3:].isdigit():
            max_id = max(max_id, int(rid[3:]))
    return f'rId{max_id + 1}'


def text_of_p(p: ET.Element) -> str:
    return ''.join(t.text or '' for t in p.iter(f'{W}t'))


def paragraph(text: str, bold: bool = False, size: int = 20, color: str = '111827', italic: bool = False) -> ET.Element:
    p = ET.Element(f'{W}p')
    ppr = ET.SubElement(p, f'{W}pPr')
    ET.SubElement(ppr, f'{W}spacing', {f'{W}before': '80', f'{W}after': '80', f'{W}line': '276', f'{W}lineRule': 'auto'})
    r = ET.SubElement(p, f'{W}r')
    rpr = ET.SubElement(r, f'{W}rPr')
    ET.SubElement(rpr, f'{W}rFonts', {f'{W}ascii': 'Malgun Gothic', f'{W}hAnsi': 'Malgun Gothic', f'{W}eastAsia': 'Malgun Gothic', f'{W}cs': 'Malgun Gothic'})
    if bold:
        ET.SubElement(rpr, f'{W}b')
    if italic:
        ET.SubElement(rpr, f'{W}i')
    ET.SubElement(rpr, f'{W}color', {f'{W}val': color})
    ET.SubElement(rpr, f'{W}sz', {f'{W}val': str(size)})
    ET.SubElement(rpr, f'{W}szCs', {f'{W}val': str(size)})
    t = ET.SubElement(r, f'{W}t')
    t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    t.text = text
    return p


def image_paragraph(rid: str, name: str, width_px: int, height_px: int, docpr_id: int) -> ET.Element:
    width_emu = 5_900_000
    height_emu = int(width_emu * height_px / width_px)
    p = ET.Element(f'{W}p')
    ppr = ET.SubElement(p, f'{W}pPr')
    ET.SubElement(ppr, f'{W}jc', {f'{W}val': 'center'})
    r = ET.SubElement(p, f'{W}r')
    drawing = ET.SubElement(r, f'{W}drawing')
    inline = ET.SubElement(drawing, f'{WP}inline', {'distT': '0', 'distB': '0', 'distL': '0', 'distR': '0'})
    ET.SubElement(inline, f'{WP}extent', {'cx': str(width_emu), 'cy': str(height_emu)})
    ET.SubElement(inline, f'{WP}effectExtent', {'l': '0', 't': '0', 'r': '0', 'b': '0'})
    ET.SubElement(inline, f'{WP}docPr', {'id': str(docpr_id), 'name': name})
    ET.SubElement(inline, f'{WP}cNvGraphicFramePr')
    graphic = ET.SubElement(inline, f'{A}graphic')
    graphic_data = ET.SubElement(graphic, f'{A}graphicData', {'uri': PIC_NS})
    pic = ET.SubElement(graphic_data, f'{PIC}pic')
    nv = ET.SubElement(pic, f'{PIC}nvPicPr')
    ET.SubElement(nv, f'{PIC}cNvPr', {'id': '0', 'name': name})
    ET.SubElement(nv, f'{PIC}cNvPicPr')
    blip_fill = ET.SubElement(pic, f'{PIC}blipFill')
    ET.SubElement(blip_fill, f'{A}blip', {f'{R}embed': rid})
    stretch = ET.SubElement(blip_fill, f'{A}stretch')
    ET.SubElement(stretch, f'{A}fillRect')
    sppr = ET.SubElement(pic, f'{PIC}spPr')
    xfrm = ET.SubElement(sppr, f'{A}xfrm')
    ET.SubElement(xfrm, f'{A}off', {'x': '0', 'y': '0'})
    ET.SubElement(xfrm, f'{A}ext', {'cx': str(width_emu), 'cy': str(height_emu)})
    ET.SubElement(sppr, f'{A}prstGeom', {'prst': 'rect'}).append(ET.Element(f'{A}avLst'))
    return p


def ensure_png_content_type(content_types: ET.Element) -> None:
    for default in content_types.findall(f'{CT}Default'):
        if default.get('Extension') == 'png':
            return
    ET.SubElement(content_types, f'{CT}Default', {'Extension': 'png', 'ContentType': 'image/png'})


def insert_diagram(docx: Path, png: Path, anchor: str, caption: str, note: str) -> None:
    tmp = docx.with_suffix('.diagram.tmp.docx')
    image_name = png.name
    image_target = f'word/media/{image_name}'

    with ZipFile(docx, 'r') as zin:
        doc_root = ET.fromstring(zin.read('word/document.xml'))
        rel_root = ET.fromstring(zin.read('word/_rels/document.xml.rels'))
        ct_root = ET.fromstring(zin.read('[Content_Types].xml'))
        all_items = {
            name: zin.read(name)
            for name in zin.namelist()
            if name not in {'word/document.xml', 'word/_rels/document.xml.rels', '[Content_Types].xml'}
            and name != image_target
        }

    body = doc_root.find(f'{W}body')
    if body is None:
        raise RuntimeError(f'body not found: {docx}')

    rid = rel_id(rel_root)
    ET.SubElement(rel_root, f'{REL}Relationship', {
        'Id': rid,
        'Type': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image',
        'Target': f'media/{image_name}',
    })
    ensure_png_content_type(ct_root)

    width_px, height_px = png_size(png)
    docpr_id = 9000 + (int(hashlib.sha256(image_name.encode()).hexdigest()[:4], 16) % 900)
    insert_nodes = [
        paragraph(caption, bold=True, size=20, color='0F172A'),
        image_paragraph(rid, image_name, width_px, height_px, docpr_id),
        paragraph(note, italic=True, size=18, color='475569'),
    ]

    children = list(body)
    insert_at = None
    for i, child in enumerate(children):
        if child.tag == f'{W}p' and anchor in text_of_p(child):
            insert_at = i + 1
            break
    if insert_at is None:
        text_seen = 0
        for i, child in enumerate(children):
            if child.tag == f'{W}p' and text_of_p(child).strip():
                text_seen += 1
            if text_seen >= 8:
                insert_at = i + 1
                break
    if insert_at is None:
        insert_at = max(0, len(children) - 1)

    for offset, node in enumerate(insert_nodes):
        body.insert(insert_at + offset, node)

    with ZipFile(tmp, 'w', ZIP_DEFLATED) as zout:
        for name, data in all_items.items():
            zout.writestr(name, data)
        zout.writestr('word/document.xml', ET.tostring(doc_root, encoding='utf-8', xml_declaration=True))
        zout.writestr('word/_rels/document.xml.rels', ET.tostring(rel_root, encoding='utf-8', xml_declaration=True))
        zout.writestr('[Content_Types].xml', ET.tostring(ct_root, encoding='utf-8', xml_declaration=True))
        zout.write(png, image_target)
    tmp.replace(docx)


def first_two_table_hashes(docx: Path) -> list[str]:
    with ZipFile(docx) as z:
        root = ET.fromstring(z.read('word/document.xml'))
    out = []
    for tbl in root.iter(f'{W}tbl'):
        out.append(hashlib.sha256(ET.tostring(tbl, encoding='utf-8')).hexdigest()[:16])
        if len(out) == 2:
            break
    return out


def main() -> None:
    DIAGRAM_DIR.mkdir(parents=True, exist_ok=True)
    for old in DIAGRAM_DIR.glob('*'):
        if old.name not in {cfg['png'].name for cfg in DOCS.values()}:
            old.unlink()

    for cfg in DOCS.values():
        render_png(Path(cfg['source_svg']), Path(cfg['png']))
        insert_diagram(Path(cfg['docx']), Path(cfg['png']), cfg['anchor'], cfg['caption'], cfg['note'])

    print('DOCX_EXISTING_DIAGRAM_INSERTION_OK')
    for key, cfg in DOCS.items():
        docx = Path(cfg['docx'])
        png = Path(cfg['png'])
        with ZipFile(docx) as z:
            names = z.namelist()
            media = [n for n in names if n.startswith('word/media/')]
            doc = z.read('word/document.xml')
            ET.fromstring(doc)
        source_hashes = first_two_table_hashes(TEMPLATES[key])
        output_hashes = first_two_table_hashes(docx)
        print(
            f'{docx.name}: source_svg={Path(cfg["source_svg"]).relative_to(ROOT)} '
            f'media={len(media)} drawing={doc.count(b"<w:drawing")} '
            f'first2_tables_identical={source_hashes == output_hashes} '
            f'png={png.name} png_size={png.stat().st_size}'
        )


if __name__ == '__main__':
    main()
