# CMS Markdown 보고서 패키지

이 패키지는 HTML 대신 Markdown 기준으로 다시 작성한 두 문서를 포함합니다.

## 포함 문서

- `live_streaming_test_results/live_streaming_test_results_narrative_v1.md`
  - 라이브 스트리밍 및 서빙 파이프라인 검증 결과 보고서
  - 논문형 구조: 초록, 서론, 데이터셋, 방법, 결과, 논의, 결론
  - SVG/Mermaid source 포함

- `pipeline_narrative/pipeline_narrative_v1.md`
  - CMS 라이브 스트리밍 및 서빙 파이프라인 구조 보고서
  - 논문형 구조: 초록, 배경, 입력 데이터, historical batch, live/replay, 저장 layer, model/promotion, application/orchestration, 결론
  - SVG/Mermaid source 포함

## Mermaid 렌더링 기준

Markdown에 Mermaid code block을 넣을 수는 있지만, 모든 viewer가 이를 그림으로 렌더링하지는 않습니다. GitHub, GitLab, Mermaid plugin이 켜진 Obsidian은 Mermaid 렌더링을 지원합니다. Discord와 일반 Markdown preview는 보통 Mermaid를 이미지로 표시하지 않습니다.

따라서 본 패키지는 Markdown 본문에서 image-link syntax로 이미 렌더링된 SVG를 참조합니다. 각 diagram의 `.mmd` source와 `.svg`도 함께 포함했습니다.
