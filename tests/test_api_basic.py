def test_health_check(client):
    """Health check API가 정상 동작하는지 (DB 연결 실패 시에도 200 반환 여부) 확인"""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "db" in data

def test_anomaly_summary(client):
    """이상탐지 요약 API가 더미 DB 접속 상태에서도 에러 JSON(200)을 반환하는지 스모크 테스트"""
    response = client.get("/anomalies/summary")
    assert response.status_code == 200
    data = response.json()
    assert "summary" in data or "error" in data

def test_forecast_models(client):
    """모델 메타데이터 조회 API 스모크 테스트"""
    response = client.get("/forecast/models")
    assert response.status_code == 200
    data = response.json()
    assert "models" in data
    assert len(data["models"]) > 0
