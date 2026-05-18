"""81개 개별 계량기 상세 분석 마크다운 보고서 자동 생성 스크립트.

Usage:
    uv run --with pandas python scripts/profiling/generate_individual_meter_report.py
"""

import pandas as pd
from pathlib import Path

# 설정
ROOT_DIR = Path(__file__).parent.parent.parent
PROFILING_DIR = ROOT_DIR / "outputs" / "profiling"
FIGURES_DIR = ROOT_DIR / "outputs" / "figures" / "meters" / "individual"
DOCS_DIR = ROOT_DIR / "docs" / "분석_기획"

DOCS_DIR.mkdir(parents=True, exist_ok=True)


def generate_markdown():
    registry_path = PROFILING_DIR / "01_meter_registry.csv"
    stats_path = PROFILING_DIR / "07_meter_statistics.csv"
    
    if not registry_path.exists() or not stats_path.exists():
        print("필요한 데이터 파일이 없습니다. meter_detailed_analysis.py를 먼저 실행하세요.")
        return
        
    df_registry = pd.read_csv(registry_path)
    df_stats = pd.read_csv(stats_path)
    
    out_md = DOCS_DIR / "07_81개_계량기_개별_분석.md"
    
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("# 07. 개별 계량기 상세 분석 리포트\n\n")
        f.write("본 문서는 Honda R&D Europe 건물에 설치된 81개 개별 계량기(Meter) 각각의 **기초 통계량**과 **주간(Weekly) 전력(P) 사용량 트렌드**를 요약한 자동 생성 리포트입니다.\n\n")
        f.write("---\n\n")
        
        # 그룹별 정렬을 위해 registry 정렬
        df_registry = df_registry.sort_values(["equipment_group", "meter_urn"])
        
        for _, row in df_registry.iterrows():
            urn = row["meter_urn"]
            group = row["equipment_group"]
            
            f.write(f"## {urn} ({group})\n\n")
            
            # 통계 테이블 작성
            meter_stats = df_stats[df_stats["meter_urn"] == urn].copy()
            if not meter_stats.empty:
                f.write("### 기초 통계량 (1h 해상도)\n\n")
                f.write("| 측정 항목 | 기록 수(시간) | 평균 | 최소 | 최대 | 무부하(0) 비율 |\n")
                f.write("|-----------|---------------|------|------|------|----------------|\n")
                
                for _, stat in meter_stats.iterrows():
                    measurement = stat['measurement']
                    count = int(stat['count'])
                    mean = f"{stat['mean']:.2f}" if pd.notnull(stat['mean']) else "N/A"
                    min_v = f"{stat['min']:.2f}" if pd.notnull(stat['min']) else "N/A"
                    max_v = f"{stat['max']:.2f}" if pd.notnull(stat['max']) else "N/A"
                    zero_ratio = f"{stat['zero_ratio']*100:.2f}%" if pd.notnull(stat['zero_ratio']) else "N/A"
                    
                    f.write(f"| {measurement} | {count:,} | {mean} | {min_v} | {max_v} | {zero_ratio} |\n")
                f.write("\n")
            else:
                f.write("통계 데이터가 존재하지 않습니다.\n\n")
                
            # 시계열 차트 첨부
            # 상대 경로는 docs/분석_기획 폴더 기준이므로 ../../outputs/... 형태
            chart_filename = f"{urn}_weekly.png"
            chart_local_path = FIGURES_DIR / chart_filename
            
            if chart_local_path.exists():
                f.write("### 주간(Weekly) 전력(Power) 트렌드\n\n")
                f.write(f"![{urn} Weekly Trend](../../outputs/figures/meters/individual/{chart_filename})\n\n")
                
            f.write("---\n\n")
            
    print(f"▶ 리포트 생성 완료: {out_md}")


if __name__ == "__main__":
    generate_markdown()
