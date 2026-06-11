# RunPod sLLM 운영 가이드

> **목적**: 최종 사내 온프레미스 배포 전에 RunPod 전용 GPU에서
> Ollama 모델 품질, VRAM 요구량, 응답 시간과 듀얼 모델 구성을 검증한다.
>
> **현재 환경 (2026-06-08)**
> - Pod ID: `dh7mur6cpdctmb` (RTX 3090 Secure Cloud)
> - 품질 모델: **Gemma4 12B** (`gemma4:12b`) — 진단·보고서·분석, thinking 활성화
> - 속도 모델: **EXAONE 3.5 7.8B** (`exaone3.5:7.8b`) — 의도 분류 등 단순 쿼리, think=false
> - 설치 모델: `exaone3.5:7.8b`, `gemma4:12b`
> - Ollama 엔드포인트: `https://dh7mur6cpdctmb-11434.proxy.runpod.net`
> - 백엔드 호출 방식: 네이티브 `/api/chat` + **듀얼 모델** (`chat(fast=False)` → Gemma4 thinking ON, `chat(fast=True)` → EXAONE think=false)

---

## 목차

1. [RunPod 계정 & 결제 설정](#1-runpod-계정--결제-설정)
2. [어떤 Pod를 열어야 하는가](#2-어떤-pod를-열어야-하는가)
3. [Pod 생성 절차 (화면 순서)](#3-pod-생성-절차-화면-순서)
4. [Ollama 설치 & 모델 수령](#4-ollama-설치--모델-수령)
5. [모델 후보 비교 & 선택 기준](#5-모델-후보-비교--선택-기준)
6. [엔드포인트 연결 (.env 설정)](#6-엔드포인트-연결-env-설정)
7. [동작 확인 (curl 테스트)](#7-동작-확인-curl-테스트)
8. [평가 하니스로 A/B 비교](#8-평가-하니스로-ab-비교)
9. [비용 관리 & Pod 중지 주의사항](#9-비용-관리--pod-중지-주의사항)
10. [최종 온프레미스 전환](#10-최종-온프레미스-전환)

---

## 1. RunPod 계정 & 결제 설정

1. **회원가입**: https://www.runpod.io → Sign Up (GitHub 소셜 로그인 가능)
2. **결제 등록**: Billing → Add Credit Card 또는 Prepay
   - 신용카드 등록 후 최소 $10 충전 권장 (테스트 전체 $3~5 수준)
   - 결제 수단 없으면 GPU Pod 생성 불가
3. **SSH 키 등록**: Settings → SSH Public Keys → 공개키 붙여넣기 → Save
   - Web Terminal보다 SSH가 안정적이므로 등록 권장
   - 등록 후 Pod **Stop → Start** 해야 키가 주입됨

### SSH 키 생성 & 등록 (Mac 기준)

**① 기존 키 확인**
```bash
cat ~/.ssh/id_ed25519.pub
# 내용이 나오면 그대로 복사해서 RunPod에 등록
```

없으면 새로 생성:
```bash
ssh-keygen -t ed25519 -C "your@email.com"
# 엔터 3번 (경로·패스프레이즈 기본값)
cat ~/.ssh/id_ed25519.pub
```

**② RunPod Settings에 등록**

RunPod → 우측 상단 프로필 → **Settings** → **SSH Public Keys** → **Add SSH Key**  
→ `ssh-ed25519 AAAA...` 전체 붙여넣기 → Save

**③ Pod에 적용**

이미 만들어진 Pod는 재시작해야 키가 주입됨:  
Pod → **Stop → Start**

키가 정상 등록됐는지 확인 (Web Terminal 또는 SSH 후):
```bash
cat ~/.ssh/authorized_keys
# ssh-ed25519 AAAA... 가 있으면 정상
```

**④ Mac에서 SSH 접속**

Pod → **Connect** 버튼 → SSH 명령어 복사:
```bash
ssh root@{ip} -p {port} -i ~/.ssh/id_ed25519
```

---

## 2. 어떤 Pod를 열어야 하는가

### GPU 선택 기준

Gemma4 12B Q4 기준 VRAM ~8GB 사용.
**테스트/평가 → RTX 3090 · 4090 추천. 최종 온프레미스 이전 전까지 사용.**

| GPU | VRAM | 시간당 비용 | 추론 속도 | 추천 용도 |
|---|---|---|---|---|
| **RTX 3090** | 24 GB | ~$0.34/h | 30~40 tok/s | **현재 사용 중** — 평가·데모 (가성비 최고) |
| **RTX 4090** | 24 GB | ~$0.74/h | 55~70 tok/s | 응답 속도를 더 줄이고 싶을 때 |
| A40 | 48 GB | ~$0.79/h | 40~50 tok/s | 13B 이상 다중 모델 테스트 시 |
| A100 SXM | 80 GB | ~$2.49/h | 100+ tok/s | 불필요 (과사양) |

> **결론**: 현재 **RTX 3090 Secure Cloud** 사용 중.
> Gemma4 12B는 thinking 활성화 기준 약 20초, think=false 실험 기준 약 14초다.

### Pod 타입

| 타입 | 설명 | 추천 여부 |
|---|---|---|
| **Secure Cloud** | 데이터센터 전용 서버, 안정적 | **선택** |
| Community Cloud | 개인 제공 GPU, 저렴하지만 불안정 | 테스트에선 비추 |

### 스토리지

- **Container Disk**: 20 GB (Ollama + 모델 저장)
  - EXAONE 3.5 7.8B Q4: 약 5 GB
  - Qwen2.5 7B Instruct Q4: 약 4.7 GB
  - 둘 다 받으면 10 GB → 여유 있게 20 GB

---

## 3. Pod 생성 절차 (화면 순서)

### 3-1. 템플릿 선택

1. 좌측 메뉴 → **Pods** → **+ Deploy**
2. **GPU** 탭에서 `RTX 3090` 검색 → Secure Cloud 선택
3. **Select Template** 화면:
   - `RunPod Pytorch 2.x` 또는 **`Ubuntu 22.04`** 선택
   - (Ollama 공식 템플릿이 있으면 그걸 써도 됨 — 검색창에 "ollama" 입력)

### 3-2. Pod 설정

> **주의**: RunPod에서 Container Disk는 Pod Stop/Restart 시에도 초기화된다.  
> 모델을 유지하려면 Volume Disk를 추가하고 `/root/.ollama`에 마운트해야 한다.

**한 세션에 끝낼 경우 (볼륨 없음)**
```
Container Disk : 20 GB
Volume Disk    : 0 GB
Expose Ports   : 11434
```
→ Pod를 멈추면 Ollama·모델 모두 삭제. 재시작 시 재설치 필요.

**여러 세션에 걸쳐 사용할 경우 (볼륨 추가 권장)**
```
Container Disk : 10 GB
Volume Disk    : 20 GB  (마운트 경로: /root/.ollama)
Expose Ports   : 11434
```
→ Stop/Terminate 후에도 모델 보존. 재시작 시 `ollama serve`만 실행하면 됨.  
→ 볼륨 비용: ~$0.07/GB/월 (20GB = 월 $1.4)

**Expose HTTP Ports 추가 방법**:
- Customize Deployment → Expose HTTP Ports → `11434` 입력 → Add

### 3-3. Deploy

- 우측 **Deploy** 버튼 클릭
- Pod 상태가 `Running`으로 바뀔 때까지 대기 (보통 30초~2분)

---

## 4. Ollama 설치 & 모델 수령

Pod가 `Running` 상태가 되면 **Connect → Start Web Terminal** (또는 SSH 접속).

### 4-1. Ollama 설치

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

설치 확인:
```bash
ollama --version
# ollama version is 0.x.x
```

### 4-2. Ollama 서버 실행

RunPod 컨테이너는 재시작 시 초기화되므로, 접속할 때마다 실행:

```bash
# 백그라운드로 실행 (외부에서 접근 가능하도록 0.0.0.0 바인딩)
OLLAMA_HOST=0.0.0.0 ollama serve &

# 서버 뜰 때까지 잠깐 대기
sleep 3
```

### 4-3. 모델 다운로드

모델은 **Ollama 공식 레지스트리**(https://ollama.com/library)에서 자동으로 받는다.  
인터넷 연결이 되는 RunPod 컨테이너 내에서 `ollama pull` 명령 한 줄로 완료.

#### 현재 설치된 모델

```bash
ollama list
# gemma4:12b        ← 현재 서비스 모델 (채택)
# exaone3.5:7.8b   ← 평가 완료 (속도 최적 백업)
```

#### 모델 pull 명령 (재설치 필요 시)

```bash
# ① Gemma4 12B — Google DeepMind, 현재 채택 (종합 9.2/10)
ollama pull gemma4:12b

# ② EXAONE 3.5 7.8B — LG AI, 한국어 특화 (종합 8.9/10, 속도 6s)
ollama pull exaone3.5:7.8b
```

다운로드 시간: 각 모델 약 4~6 GB → 수분 소요 (RunPod 대역폭 빠름).

다운로드 확인:
```bash
ollama list
# NAME                        ID              SIZE    MODIFIED
# exaone3.5:7.8b              xxxx            5.0 GB  ...
# qwen2.5:7b-instruct         xxxx            4.7 GB  ...
```

### 4-4. 빠른 동작 확인 (터미널 내)

```bash
ollama run gemma4:12b "안녕, 간단히 자기소개 해줘"
# 응답이 한국어로 나오면 정상
```

---

## 5. 모델 비교 결과 (평가 완료, 2026-06-08)

### 전체 비교표 (33문항 · 5축 · 10점 만점)

| 모델 | 종합 | 한국어 | 형식 | 근거성 | 논리성 | 실용성 | 속도 | 상태 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **Gemma4 12B** | **9.2** | 9.2 | **9.3** | 8.9 | **9.4** | 9.2 | ~14s | ✅ **채택** |
| GPT-4o | 9.1 | 9.1 | 9.0 | 8.8 | 9.4 | 9.3 | 3.4s | ❌ 클라우드 |
| EXAONE 3.5 7.8B | 8.9 | 8.9 | 8.6 | 8.8 | 9.0 | 9.0 | **6.0s** | 백업 |
| GPT-4o-mini | 8.9 | 8.9 | 8.8 | 8.6 | 9.0 | 9.2 | 4.7s | ❌ 클라우드 |
| Gemma3 12B | 8.7 | 9.0 | 8.2 | 8.3 | 9.1 | 8.8 | 7.7s | 비채택 |
| Qwen2.5 7B | 2.4 | 3.9 | 3.8 | 4.5 | 0.0 | 0.0 | 4.8s | ❌ 중국어 전환 |

상세 선정 근거: [`model_selection.md`](model_selection.md)

### Gemma4 thinking 모드 주의사항

Gemma4는 **reasoning/thinking 모델**로, 기본적으로 답변 전 추론 과정을 생성합니다.

- `/v1/chat/completions` (OpenAI 호환): `think` 파라미터 무시됨 → thinking 포함 시 응답 ~20초
- `/api/chat` (Ollama 네이티브): `"think": false` 지원 → thinking 비활성화 → ~14초

**백엔드 `llm_client.py` 듀얼 모델 라우팅 (이미 적용됨)**:

```python
# fast=True  → EXAONE (속도 경로: 의도 분류 등)
# fast=False → Gemma4 (품질 경로: 진단·보고서·분석, thinking 활성화)
def chat(messages, max_tokens=1024, fast=False):
    model = LLM_MODEL_FAST if fast else LLM_MODEL   # EXAONE vs Gemma4
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }
    if fast:
        payload["think"] = False   # EXAONE: thinking 없음 → 명시적 비활성화
    # Gemma4: think 파라미터 생략 → thinking 활성화 (품질 극대화)
    resp = httpx.post(f"{ollama_base_url}/api/chat", json=payload, timeout=120)
    return resp.json()["message"]["content"]
```

---

## 6. 엔드포인트 연결 (.env 설정)

### 현재 적용된 설정

```env
# ── sLLM (RunPod + Ollama) — 듀얼 모델 ───────────────────────────────
LLM_PROVIDER=ollama
LLM_MODEL=gemma4:12b            # 품질 경로 (진단·보고서·분석, thinking 활성화)
LLM_MODEL_FAST=exaone3.5:7.8b  # 속도 경로 (의도 분류 등, think=false)
OLLAMA_URL=https://dh7mur6cpdctmb-11434.proxy.runpod.net/v1
```

> Pod 재시작 시 URL이 바뀔 수 있음. 변경 시 `OLLAMA_URL`만 교체 → `docker compose restart backend`

### 새 Pod 연결 방법

Pod 목록에서 해당 Pod의 **Connect** 버튼 클릭 →
**HTTP Service** 섹션에서 포트 `11434`의 URL 확인:

```
https://{pod-id}-11434.proxy.runpod.net
```

`.env` 수정:
```env
OLLAMA_URL=https://{pod-id}-11434.proxy.runpod.net/v1
```

### 듀얼 모델 구성 설명

두 모델이 동시에 운영됩니다. `LLM_MODEL` / `LLM_MODEL_FAST` 모두 설정해야 합니다.

```env
LLM_MODEL=gemma4:12b            # fast=False 경로 → 진단·보고서·이상탐지·RAG
LLM_MODEL_FAST=exaone3.5:7.8b  # fast=True  경로 → 의도 분류 (orchestrator)
```

단일 모델로 롤백하려면 `LLM_MODEL_FAST`를 `LLM_MODEL`과 동일하게 설정:

```env
LLM_MODEL=gemma4:12b
LLM_MODEL_FAST=gemma4:12b  # 모든 경로 Gemma4로 통일
```

`.env` 변경 후: `docker compose restart backend`

---

## 7. 동작 확인 (curl 테스트)

### Ollama API 직접 테스트

```bash
# 모델 목록 확인
curl https://{pod-id}-11434.proxy.runpod.net/v1/models

# 응답 예시
{
  "data": [
    {"id": "exaone3.5:7.8b", ...},
    {"id": "qwen2.5:7b-instruct", ...}
  ]
}
```

### 네이티브 API 채팅 테스트 (권장 — think=false 지원)

```bash
curl https://dh7mur6cpdctmb-11434.proxy.runpod.net/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma4:12b",
    "messages": [{"role": "user", "content": "3호 압축기 전압이 381V로 정격 대비 5% 낮습니다. 원인과 조치를 진단해 주세요."}],
    "stream": false,
    "think": false
  }'
```

응답에서 `message.content`에 한국어 진단 내용이 `🩺 진단 결과` 포맷으로 오면 정상.

> 참고: `/v1/chat/completions` (OpenAI 호환)도 동작하지만 `think: false` 무시됨 — 직접 테스트 용도로만 사용.

### 백엔드 통합 테스트

```bash
# 백엔드가 올라와 있을 때
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "3호기 이상탐지 현황 알려줘"}'
```

---

## 8. 평가 하니스로 A/B 비교

아래 스크립트를 `dev/eval/harness.py`로 만들어 모델별 품질을 정량 비교한다.

```python
"""
eval/harness.py — sLLM 모델 A/B 평가
실행: LLM_MODEL=exaone3.5:7.8b python dev/eval/harness.py
"""
import os, json, time
from backend.src.agents.llm_client import chat

PROMPTS = [
    {
        "id": "cms_diagnosis",
        "messages": [
            {"role": "system", "content": "당신은 CMS 설비 진단 전문가입니다."},
            {"role": "user",   "content": "3호 압축기: 전압 381V(정격 400V), 전류 42A(정격 38A), 역률 0.72. 진단 결과를 🩺/🔍/✅ 섹션으로 작성하세요."},
        ],
    },
    {
        "id": "anomaly_explain",
        "messages": [
            {"role": "user", "content": "2024-03-15 14:32에 감지된 이상 패턴: 전류 스파이크 +35%, 역률 급락 0.91→0.67. 원인과 긴급도를 설명하세요."},
        ],
    },
    {
        "id": "report_narrative",
        "messages": [
            {"role": "user", "content": "이번 주 설비 운영 요약: 가동률 94.2%, 이상탐지 3건(2건 경미·1건 주의), 에너지 효율 전주 대비 +1.8%. 경영진 보고용 요약 2~3문장으로 작성하세요."},
        ],
    },
    {
        "id": "rag_query",
        "messages": [
            {"role": "user", "content": "역률이 0.7 이하로 떨어지면 어떤 문제가 발생하나요?"},
        ],
    },
    {
        "id": "intent_classify",
        "messages": [
            {"role": "user", "content": "사용자 메시지: '지난달 전력 소비 패턴 보여줘'. 의도를 forecast/anomaly/cms/rag/report 중 하나로만 답하세요."},
        ],
    },
]

def run():
    model = os.getenv("LLM_MODEL", "exaone3.5:7.8b")
    results = []
    for p in PROMPTS:
        t0 = time.time()
        output = chat(p["messages"], max_tokens=512)
        elapsed = round(time.time() - t0, 2)
        results.append({"id": p["id"], "model": model, "elapsed": elapsed, "output": output})
        print(f"\n{'='*60}")
        print(f"[{p['id']}] {model} ({elapsed}s)")
        print(output)

    out_path = f"dev/eval/results_{model.replace(':', '_')}.json"
    os.makedirs("eval", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {out_path}")

if __name__ == "__main__":
    run()
```

**실행 순서**:

```bash
# 1) gpt-4o 기준선 먼저
LLM_PROVIDER=openai  LLM_MODEL=gpt-4o              python dev/eval/harness.py

# 2) EXAONE 테스트
LLM_PROVIDER=ollama  LLM_MODEL=exaone3.5:7.8b      python dev/eval/harness.py

# 3) Qwen2.5 테스트
LLM_PROVIDER=ollama  LLM_MODEL=qwen2.5:7b-instruct python dev/eval/harness.py

# 4) dev/eval/results_*.json 파일 열어 육안 비교
```

---

## 9. 비용 관리 & Pod 중지 주의사항

### 비용 구조

- Pod가 `Running` 상태이면 GPU를 사용하지 않아도 **시간당 과금**.
- 작업이 없을 때는 반드시 **Stop** 해야 한다.

| 상태 | 과금 여부 |
|---|---|
| Running | GPU 요금 100% 청구 |
| Stopped | 스토리지 요금만 (~$0.10/h per 20GB) |
| Terminated | 과금 없음, 데이터 삭제 |

### 주의사항

```
평가 끝나면 → Pods → Stop (일시정지, 모델 유지)
장기간 사용 안 할 때 → Terminate (데이터 삭제, 과금 완전 중지)
```

모델은 `Terminate` 시 삭제되므로, 재사용할 때는 `ollama pull`을 다시 실행.  
→ 모델 다운로드는 5~10분 소요. 비용 절감 vs 편의성 트레이드오프.

### 예상 총 비용

| 작업 | 시간 | 예상 비용 (RTX 3090) |
|---|---|---|
| Ollama 설치 + 모델 2개 pull | ~30분 | ~$0.17 |
| 평가 하니스 5개 프롬프트 × 3모델 | ~20분 | ~$0.11 |
| few-shot 보정 반복 테스트 | ~1시간 | ~$0.34 |
| **합계** | **~2시간** | **~$0.62** |

---

## 10. 최종 온프레미스 전환

RunPod에서 모델 선택·보정이 끝나면, 공장 내부 GPU 서버로 이전.  
절차는 RunPod과 동일 (Ollama 설치 → 모델 pull) — `.env` 한 줄만 교체.

```env
# RunPod (평가·데모)
OLLAMA_URL=https://{pod-id}-11434.proxy.runpod.net/v1

# 온프레미스 (최종 배포)
OLLAMA_URL=http://192.168.x.x:11434/v1
```

코드 변경 없음. 동일한 Ollama OpenAI 호환 API.

---

## 빠른 체크리스트

```
[완료] RunPod 계정 생성 & 결제
[완료] RTX 3090 Secure Cloud Pod 생성 (11434 포트 오픈)
[완료] Ollama 설치 → OLLAMA_HOST=0.0.0.0 ollama serve
[완료] ollama pull exaone3.5:7.8b
[완료] ollama pull gemma4:12b
[완료] 6개 모델 평가 → Gemma4 12B 채택 (9.2/10)
[완료] .env LLM_MODEL=gemma4:12b + LLM_MODEL_FAST=exaone3.5:7.8b 적용 → 백엔드 재시작
[완료] llm_client.py 네이티브 API + 듀얼 모델 (chat(fast=) 파라미터) 적용
[완료] orchestrator.py 의도 분류 fast=True 적용
[ ] 온프레미스 전환: OLLAMA_URL → 공장 서버 IP 교체
```

---

> 관련 파일:
> - `backend/src/agents/llm_client.py` — ollama 프로바이더 (이미 구현)
> - `.env` / `.env.example` — `LLM_PROVIDER`, `LLM_MODEL`, `OLLAMA_URL`
> - [`plan.md`](plan.md) — 전체 sLLM 전환 전략
