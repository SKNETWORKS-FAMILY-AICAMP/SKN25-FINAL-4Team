"""
온톨로지 문장을 임베딩해서 pgvector에 저장.
임베딩: sentence-transformers (로컬, 무료)
LLM: Anthropic API (ANTHROPIC_API_KEY 하나만 필요)
"""

import os
import sys
import hashlib
import psycopg2
from psycopg2.extras import execute_values
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from energy_ontology import build_graph, to_rag_sentences

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/energy_db")

# multilingual-e5-large: 한국어 지원, 1024차원
EMBED_MODEL_NAME = "intfloat/multilingual-e5-large"
_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        print(f"임베딩 모델 로드 중: {EMBED_MODEL_NAME}")
        _model = SentenceTransformer(EMBED_MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = get_model()
    # e5 모델은 "passage: " 접두어 사용
    prefixed = [f"passage: {t}" for t in texts]
    embeddings = model.encode(prefixed, normalize_embeddings=True, show_progress_bar=True)
    return embeddings.tolist()


def embed_query(text: str) -> list[float]:
    model = get_model()
    # 검색 시에는 "query: " 접두어
    result = model.encode([f"query: {text}"], normalize_embeddings=True)
    return result[0].tolist()


def init_table(conn):
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ontology_knowledge (
                id        SERIAL PRIMARY KEY,
                content   TEXT NOT NULL,
                hash      CHAR(64) UNIQUE,
                embedding vector(1024)
            );
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS ontology_embedding_idx "
            "ON ontology_knowledge USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);"
        )
    conn.commit()


def index_ontology(force: bool = False):
    """온톨로지 문장을 pgvector에 저장. force=True면 기존 데이터 덮어씀."""
    g = build_graph()
    sentences = to_rag_sentences(g)
    print(f"온톨로지 문장 {len(sentences)}개 준비")

    conn = psycopg2.connect(DB_URL)
    init_table(conn)

    if force:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ontology_knowledge;")
        conn.commit()

    with conn.cursor() as cur:
        cur.execute("SELECT hash FROM ontology_knowledge;")
        existing = {row[0] for row in cur.fetchall()}

    new_sentences, new_hashes = [], []
    for s in sentences:
        h = hashlib.sha256(s.encode()).hexdigest()
        if h not in existing:
            new_sentences.append(s)
            new_hashes.append(h)

    if not new_sentences:
        print("이미 최신 상태 — 추가할 문장 없음")
        conn.close()
        return

    print(f"{len(new_sentences)}개 새 문장 임베딩 중...")
    embeddings = embed_texts(new_sentences)

    rows = [(s, h, e) for s, h, e in zip(new_sentences, new_hashes, embeddings)]
    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO ontology_knowledge (content, hash, embedding) VALUES %s ON CONFLICT (hash) DO NOTHING;",
            rows,
            template="(%s, %s, %s::vector)",
        )
    conn.commit()
    conn.close()
    print(f"pgvector 저장 완료: {len(rows)}개")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="기존 데이터 삭제 후 전체 재색인")
    args = parser.parse_args()
    index_ontology(force=args.force)
