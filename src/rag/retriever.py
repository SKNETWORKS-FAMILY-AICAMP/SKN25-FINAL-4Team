"""
RAG 검색기 + 문서 라우팅

라우팅 기준:
  혼다(독일) 데이터 분석 시 → 독일/공통 문서 우선
  한국 데이터 분석 시       → 한국/공통 문서 우선

문서 소스 (config의 rag_doc_filter 기준):
  common:  UNEP 산업 에너지 효율 가이드, IEA 냉난방 핸드북
  korea:   한국에너지공단 자료, 에너지진단 안내서, 보일러 가이드
  germany: BMWK 에너지 효율 전략
"""
# TODO: pgvector 연동 후 구현
