"""DWD(독일 기상청) 기상 데이터 보완 파이프라인.

논문의 EMS 관측소(WeatherStation.Weather) 기상 데이터 결측을 메우기 위해
독일 기상청(DWD) Climate Data Center의 Offenbach(07341) 데이터를 다운로드하고 병합한다.

Usage:
    uv run scripts/ingest/dwd_weather_ingest.py
"""

import io
import re
import zipfile
from pathlib import Path

import pandas as pd
import requests

# 논문 명시 관측소 ID (Offenbach-Wetterpark)
STATION_ID = "07341"

# DWD CDC Open Data Base URLs
DWD_BASE_URL = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly"
URL_TEMP = f"{DWD_BASE_URL}/air_temperature/historical/"
URL_SOLAR = f"{DWD_BASE_URL}/solar/"


def find_zip_url(base_url: str, station_id: str) -> str:
    """해당 관측소의 최신 zip 파일 URL을 DWD 디렉토리 리스팅에서 찾는다."""
    resp = requests.get(base_url)
    resp.raise_for_status()
    html = resp.text

    # 예: href="stundenwerte_TU_07341_19550101_20231231_hist.zip"
    pattern = rf'href="([^"]+_{station_id}_[^"]+\.zip)"'
    match = re.search(pattern, html)
    if not match:
        raise ValueError(f"Station {station_id}에 대한 zip 파일을 {base_url}에서 찾을 수 없습니다.")
    
    filename = match.group(1)
    return base_url + filename


def download_and_extract_df(zip_url: str) -> pd.DataFrame:
    """zip 파일을 다운로드하고 내부의 produkt_*.txt 데이터를 DataFrame으로 파싱한다."""
    print(f"다운로드 중: {zip_url}")
    resp = requests.get(zip_url)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        # 데이터가 있는 produkt_*.txt 파일 찾기
        data_file = [name for name in z.namelist() if name.startswith("produkt_") and name.endswith(".txt")][0]
        with z.open(data_file) as f:
            # DWD 데이터는 ; 구분자 사용
            df = pd.read_csv(f, sep=";", skipinitialspace=True, na_values=["-999"])
    
    # 공백 제거
    df.columns = df.columns.str.strip()
    return df


def ingest_dwd_weather():
    """기온과 일사량 데이터를 다운로드하여 병합한다."""
    print("DWD 기상 데이터 수집 시작...")

    # 1. 기온 (Air Temperature)
    temp_url = find_zip_url(URL_TEMP, STATION_ID)
    df_temp = download_and_extract_df(temp_url)
    
    # 필요한 컬럼: MESS_DATUM (시간), TT_TU (기온 °C)
    df_temp = df_temp[["MESS_DATUM", "TT_TU"]].rename(columns={"TT_TU": "Ta"})
    
    # 2. 일사량 (Solar)
    # solar는 historical과 recent가 나뉘지 않고 하나의 폴더에 있음
    solar_url = find_zip_url(URL_SOLAR, STATION_ID)
    df_solar = download_and_extract_df(solar_url)

    # 필요한 컬럼: MESS_DATUM, FG_LBERG (Global irradiance in J/cm²)
    # Igm(W/m²) = J/cm² * 10000 / 3600 = J/cm² * 2.7777...
    df_solar = df_solar[["MESS_DATUM", "FG_LBERG"]].copy()
    df_solar["Igm"] = df_solar["FG_LBERG"] * (10000 / 3600)
    df_solar = df_solar[["MESS_DATUM", "Igm"]]

    # 3. 병합 (Merge)
    print("데이터 병합 및 포맷팅...")
    df_merged = pd.merge(df_temp, df_solar, on="MESS_DATUM", how="outer")
    
    # MESS_DATUM 포맷: YYYYMMDDHH -> datetime
    df_merged["ts"] = pd.to_datetime(df_merged["MESS_DATUM"], format="%Y%m%d%H").dt.tz_localize("UTC")
    df_merged = df_merged.drop(columns=["MESS_DATUM"])

    # 정렬 및 결측치 확인
    df_merged = df_merged.sort_values("ts").reset_index(drop=True)
    
    # 저장
    out_dir = Path("outputs/tables")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"dwd_weather_{STATION_ID}.csv"
    
    df_merged.to_csv(out_path, index=False)
    print(f"완료! 저장 경로: {out_path}")
    print(f"기간: {df_merged['ts'].min()} ~ {df_merged['ts'].max()}")
    print(f"총 Row 수: {len(df_merged)}")


if __name__ == "__main__":
    ingest_dwd_weather()
