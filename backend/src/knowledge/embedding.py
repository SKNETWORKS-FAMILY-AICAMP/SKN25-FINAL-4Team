"""
임베딩 유틸리티.
sentence-transformers 로컬 모델로 텍스트 임베딩 생성.
문서 RAG 검색 등에서 공통으로 사용.
"""

from sentence_transformers import SentenceTransformer

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
    """여러 텍스트를 임베딩. 인덱싱 시 사용."""
    model = get_model()
    prefixed = [f"passage: {t}" for t in texts]
    embeddings = model.encode(prefixed, normalize_embeddings=True, show_progress_bar=True)
    return embeddings.tolist()


def embed_query(text: str) -> list[float]:
    """단일 질문을 임베딩. 검색 시 사용."""
    model = get_model()
    result = model.encode([f"query: {text}"], normalize_embeddings=True)
    return result[0].tolist()
