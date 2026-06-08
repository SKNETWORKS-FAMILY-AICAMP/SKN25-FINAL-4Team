"""
RAG 지식베이스 자동 임베딩 및 DB 적재 스크립트.
backend/docs/kb/ 디렉토리 내의 .txt, .md 파일을 청킹 및 임베딩하여 PostgreSQL pgvector DB에 적재합니다.
"""

import os
import sys
import hashlib
from pathlib import Path
from datetime import datetime

# 프로젝트 루트 및 src 경로 추가
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend" / "src"))

import psycopg2
from dotenv import load_dotenv
from knowledge.embedding import embed_query

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")

def _local_conn():
    if DB_URL:
        return psycopg2.connect(DB_URL)
    return psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


# 청크 지식 데이터 디렉토리 설정
KB_DIR = ROOT / "backend" / "docs" / "kb"


def ensure_sample_document():
    """kb 디렉토리가 비어있다면 가이드용 샘플 문서를 생성합니다."""
    KB_DIR.mkdir(parents=True, exist_ok=True)
    sample_file = KB_DIR / "sample_energy_guide.txt"
    
    if not any(KB_DIR.iterdir()):
        sample_content = """# Honda R&D Europe Offenbach 에너지 절감 표준 가이드라인

## 1. 냉방 효율 개선 및 COP 하락 조치
- 하절기 냉방 장치(Chiller) 가동 시 COP가 2.0 이하로 급락하는 이상 현상은 주로 응축기 코일의 열화, 냉매 부족, 혹은 스케일 누적으로 인해 발생합니다.
- 조치 방법: 현장 정비팀은 즉시 냉온수 배관의 스케일을 세척하고 냉각탑 수질을 검사해야 합니다. COP가 1.5 이하(심각 등급)로 하락할 경우 강제 차단 후 긴급 점검을 실시합니다.

## 2. CHP(열병합발전) 하절기 운영 지침
- 열병합발전설비(CHP)는 하절기 외기 온도 상승 시 발전 및 열회수 효율이 저하되는 물리적 특성이 있습니다.
- 조치 방법: 하절기(6월~8월)에는 무리한 풀 부하 운전을 지양하고, 주간 전력 피크 타임인 10:00 ~ 16:00 사이에 출력을 80% 이상으로 모듈레이션 조절하여 계통 전력 피크 컷을 보조하도록 세팅합니다.

## 3. 야간 및 주말 대기 전력 차단 프로토콜
- 연구소 퇴근 시간(18:00) 이후 및 주말(토요일, 일요일) 공휴일에는 가동하지 않는 공기압축기(Compressor)와 환기 송풍기(Ventilator) 부하가 지속되어 대기 전력 누수가 발생할 수 있습니다.
- 조치 방법: 당직 근무자는 퇴근 시 공기압축기 공급 메인 밸브를 차단하고, 환기 송풍기의 풍량을 최소 에코(Eco) 모드로 전환해야 합니다. 이 조치만으로 연간 약 15MWh 이상의 전력 낭비를 방지할 수 있습니다.

## 4. 태양광(PV) 야간 출력 오류 조치
- 태양광 발전설비(PV)의 전력 계측값이 야간(22:00 ~ 06:00)에 500W 이상의 양수 값으로 계측되는 PVNightNonZero 현상은 센서 오결선 또는 인버터 절전 제어 릴레이 고장으로 판단됩니다.
- 조치 방법: 인버터 룸 제어 릴레이 접점을 검사하고, 1주일 이상 현상 지속 시 센서 캘리브레이션을 즉시 접수해 갱신해야 합니다.
"""
        sample_file.write_text(sample_content, encoding="utf-8")
        print(f"[KB-Ingest] 샘플 지식 문서를 생성했습니다: {sample_file}")


def split_text(text: str, chunk_size: int = 400, overlap: int = 50) -> list[str]:
    """텍스트를 문장/줄 바꿈 기준으로 청킹 수행"""
    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = ""
    
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
            
        if len(current_chunk) + len(p) <= chunk_size:
            current_chunk += p + "\n\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            # 오버랩 보존을 위해 현재 문단 앞부분 슬라이싱 또는 이전 문단 꼬리 보존
            if len(p) > chunk_size:
                # 단일 문단이 너무 크면 강제 분할
                for i in range(0, len(p), chunk_size - overlap):
                    chunks.append(p[i:i + chunk_size].strip())
                current_chunk = ""
            else:
                current_chunk = p + "\n\n"
                
    if current_chunk:
        chunks.append(current_chunk.strip())
        
    return chunks


def init_rag_schema():
    """vector 확장 기능 설치 및 energy_documents 테이블 구축 (1024차원)"""
    with _local_conn() as conn:
        with conn.cursor() as cur:
            # pgvector 활성화
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                conn.commit()
                print("[KB-Ingest] pgvector 확장 팩이 활성화되었습니다.")
            except Exception as e:
                print(f"[KB-Ingest] Warning: pgvector 활성화 실패 (이미 활성화되었거나 권한 부족): {e}")
                conn.rollback()

            # 이전 잘못 생성된 1536차원 테이블 호환성 해결을 위해 테이블 제거 후 1024차원으로 재생성
            try:
                cur.execute("DROP TABLE IF EXISTS energy_documents CASCADE;")
                conn.commit()
            except Exception:
                conn.rollback()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS energy_documents (
                    id          SERIAL PRIMARY KEY,
                    content     TEXT NOT NULL,
                    embedding   vector(1024), -- multilingual-e5-large 임베딩 규격 1024 차원
                    source      TEXT,
                    hash        TEXT UNIQUE,  -- 중복 삽입 차단용 md5 해시
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                );
            """)
            conn.commit()
            print("[KB-Ingest] energy_documents 테이블 스키마 검증 완료 (1024차원).")


def ingest_documents():
    """지식 문서 스캔 ➔ 청킹 ➔ 임베딩 ➔ DB 벌크 적재 실행"""
    ensure_sample_document()
    init_rag_schema()
    
    # 1. 임베딩 모델 사전 로드 및 워밍업 (DB 커넥션을 열기 전에 모델 다운로드 완료)
    print("[KB-Ingest] 임베딩 모델 로드 및 워밍업 시작...")
    try:
        embed_query("warmup")
        print("[KB-Ingest] 임베딩 모델 로드 성공.")
    except Exception as e:
        print(f"[KB-Ingest] 임베딩 모델 워밍업 실패: {e}")
        return

    kb_files = list(KB_DIR.glob("*.txt")) + list(KB_DIR.glob("*.md"))
    if not kb_files:
        print("[KB-Ingest] 적재할 문서 파일(.txt, .md)이 없습니다.")
        return

    print(f"[KB-Ingest] 스캔된 지식 문서 개수: {len(kb_files)}개")
    
    total_inserted = 0
    with _local_conn() as conn:
        with conn.cursor() as cur:
            for file_path in kb_files:
                try:
                    text = file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    print(f"[KB-Ingest] 인코딩 오류로 건너뜀: {file_path.name}")
                    continue
                
                print(f"[KB-Ingest] 파일 처리 중: {file_path.name} ({len(text)}자)")
                chunks = split_text(text)
                print(f"  └─ 청킹 생성: {len(chunks)}개 청크 분할 완료")
                
                for chunk in chunks:
                    # 중복 적재 방지용 MD5 해시 생성
                    md5_hash = hashlib.md5(chunk.encode("utf-8")).hexdigest()
                    
                    # 이미 존재하는 해시인지 사전 검사
                    cur.execute("SELECT id FROM energy_documents WHERE hash = %s;", (md5_hash,))
                    if cur.fetchone():
                        continue
                        
                    try:
                        # 임베딩 생성 (OpenAI API 호출 또는 로컬 모델 사용)
                        embedding = embed_query(chunk)
                        vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
                        
                        cur.execute("""
                            INSERT INTO energy_documents (content, embedding, source, hash)
                            VALUES (%s, %s::vector, %s, %s)
                            ON CONFLICT (hash) DO NOTHING;
                        """, (chunk, vec_str, file_path.name, md5_hash))
                        total_inserted += cur.rowcount
                    except Exception as e:
                        print(f"  └─ 임베딩 변환 및 DB 적재 오류: {e}")
                        
            conn.commit()
            
    print(f"[{datetime.now()}] [KB-Ingest] RAG 지식 임베딩 적재가 완료되었습니다. (신규 추가: {total_inserted}건)")


if __name__ == "__main__":
    ingest_documents()
