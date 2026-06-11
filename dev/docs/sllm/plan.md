# sLLM 전환 계획 (Ollama)

> 목적: 회사 설비·에너지 데이터를 외부 LLM 서비스로 보내지 않는 사내 온프레미스 sLLM(Ollama) 구성 검증.
> **2026-06-08 현재 상태**: Gemma4 12B를 채택하고 RunPod RTX 3090에서 온프레미스 이전 가능성을 검증하는 PoC 진행 중.
> 상세 결과: [`model_selection.md`](model_selection.md) / [`progress.md`](progress.md) / `dev/eval/results/`

---

## 1. 현재 구조 — 전환이 쉬운 이유

- **LLM 호출이 단일 진입점**: 모든 호출이 `backend/src/agents/llm_client.py`의 `chat(messages, max_tokens, fast=False)`를 거침 (사용처 7곳: orchestrator 의도분류 / rag / cms 진단 / anomaly / forecast / reporting / report narrative).
- `chat()`에 이미 **OpenAI 호환 분기**가 있음(gemini가 OpenAI 호환 base_url 사용). **Ollama도 OpenAI 호환 API(`/v1`)** 라 같은 분기 재사용.
- **임베딩(RAG)은 이미 로컬**: `knowledge/embedding.py`가 sentence-transformers → 클라우드 의존 없음. RAG는 전환 영향 없음.
- env 전환: `LLM_PROVIDER` / `LLM_MODEL`만 바꾸면 전 사용처 적용.

→ **코드 변경 최소(약 5줄) + env 플립**으로 전환 가능.

---

## 2. 구현 단계

### ① llm_client에 ollama 프로바이더 추가 (`_get_client`)
```python
elif LLM_PROVIDER == "ollama":
    from openai import OpenAI
    client = OpenAI(
        base_url=os.getenv("OLLAMA_URL", "http://ollama:11434/v1"),
        api_key="ollama",   # 더미
    )
```
`chat()`의 `else`(OpenAI 호환) 분기가 그대로 처리.

### ② .env
```env
LLM_PROVIDER=ollama
LLM_MODEL=gemma4:12b            # 품질 경로 (진단·보고서·분석)
LLM_MODEL_FAST=exaone3.5:7.8b  # 속도 경로 (의도 분류·단순 쿼리)
OLLAMA_URL=http://ollama:11434/v1
```

### ③ docker-compose에 ollama 서비스
```yaml
ollama:
  image: ollama/ollama
  volumes: [ollama_models:/root/.ollama]
  ports: ["11434:11434"]
  # GPU: deploy.resources.reservations.devices (nvidia)
backend:
  depends_on: [ollama]
```
모델 1회 pull: `docker exec ollama ollama pull exaone3.5:7.8b`

---

## 3. 모델 평가 결과 (6개 모델, 33문항, 5축 10점 만점)

| 모델 | 종합 | 속도 | 상태 |
|---|---|---|---|
| **Gemma4 12B** (Google) | **9.2** | ~20s (thinking) | ✅ **품질 경로 (진단·보고서)** |
| GPT-4o (OpenAI) | 9.1 | 3.4s | ❌ 클라우드 (보안 이슈) |
| **EXAONE 3.5 7.8B** (LG) | **8.9** | **6.0s** | ✅ **속도 경로 (의도 분류)** |
| GPT-4o-mini | 8.9 | 4.7s | ❌ 클라우드 |
| Gemma3 12B (Google) | 8.7 | 7.7s | 비채택 |
| Qwen2.5 7B (Alibaba) | 2.4 | 4.8s | ❌ 비채택 — 중국어 전환 |

- 채택 근거 상세: [`model_selection.md`](model_selection.md)
- 평가 결과 파일: `dev/eval/results/`
- 제약: **GPU VRAM** (Gemma4 12B Q4 ≈ 8GB). RunPod RTX 3090에서 PoC 검증 중, 온프레미스 서버로 전환 예정.

---

## 4. 평가 (모델 선택 기준)

**완료된 평가 방식** — `dev/eval/harness.py`

- **33개 시나리오** (진단·보고서·이상·의도분류·계절성·안전성 등)
- **LLM-as-Judge**: GPT-4o가 심사위원으로 1~10점 자동 채점
- **5개 기준**: 한국어 품질 / 형식 준수 / 근거성(수치 인용) / 논리성 / 실용성
- **6개 모델 비교**: Gemma4(9.2) / GPT-4o(9.1) / EXAONE(8.9) / GPT-4o-mini(8.9) / Gemma3(8.7) / Qwen(2.4)

결과: 내부 평가셋에서 Gemma4 12B 9.2, GPT-4o 9.1로 대등한 수준을 확인.

---

## 5. 품질 보정 전략 — 우선순위

로컬 7~8B는 클라우드보다 추론·형식 준수가 약함. 보정은 ROI 순으로:

1. **수동 few-shot (1순위)** ⭐ — 진단 프롬프트에 이상적 예시 1~2개 → 형식 준수+근거 인용 극적 개선. 가장 싸고 효과적.
2. **자동 프롬프트 최적화(DSPy/APE)** — 평가셋+지표 있을 때만. #1로 부족하면 도입.
3. **LoRA 파인튜닝** — 최후. 위 둘로도 형식/한국어가 안 되고 + gpt-4o 증류 데이터셋을 만들 수 있을 때만.

**가중치 파인튜닝은 기본적으로 보류**: 학습 데이터 없음, 도메인은 프롬프트/RAG로 이미 주입, 데모 일정 대비 비용·위험 큼.

역할: 프롬프트/few-shot/DSPy = 에이전트 레이어(keun). LoRA = ML·GPU 인프라(팀원).

---

## 6. 전환 절차 (안전)

```
평가 하니스 구축(고정 프롬프트 + gpt-4o 기준선)
  → ollama 프로바이더 코드 추가 (플립만 남기고 미리)
  → 후보 2~3개 pull → 하니스로 A/B 비교 → 모델 선택
  → 부족한 진단/RAG 프롬프트에 few-shot 보정
  → (필요시) DSPy 자동 최적화 → (최후) LoRA
  → LLM_PROVIDER=ollama 로 최종 플립
```
임베딩 로컬이라 RAG 영향 없음. 라우터가 `def`(스레드풀 병렬)라 로컬 추론 지연도 다른 요청 안 막음. 타임아웃만 여유 있게.

---

## 7. 현재 상태 / 다음 액션

- [x] 본 계획 문서화
- [x] 평가 하니스 작성 — `dev/eval/harness.py` (33문항, 5축 10점 척도, GPT-4o judge)
- [x] ollama 프로바이더 코드 추가 — `agents/llm_client.py` (네이티브 `/api/chat` + `think: false`)
- [x] RunPod RTX 3090 환경 구축 → 6개 모델 평가 → **Gemma4 12B 최종 선정**
- [x] few-shot 보정 (cms/reporting/report 에이전트) + `.env` 플립 완료
- [x] 프롬프트 엔지니어링 (도메인 지식 확장, 버그 9건 수정, 오프토픽 거절 강화)
- [x] Gemma4 thinking 버그 수정 — harness `max_tokens=600→3000`, llm_client 네이티브 API 적용
- [x] **듀얼 모델 아키텍처** — `chat(fast=False)` 파라미터, `LLM_MODEL_FAST` 환경 변수, orchestrator fast=True (2026-06-08)
- [ ] **온프레미스 전환**: RunPod → 공장 서버 `OLLAMA_URL` 교체 (배포 단계)

> 관련 문서: [`model_selection.md`](model_selection.md) (선정 근거) · [`progress.md`](progress.md) (진행 요약) · [`runpod_guide.md`](runpod_guide.md) (운영 가이드)
> 관련 코드: `agents/llm_client.py` · `knowledge/domain_knowledge.py` · `dev/eval/harness.py`
