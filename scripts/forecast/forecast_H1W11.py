# H1.W11 P값 1h 예측 노트북
# EMS 프로젝트 예측 모델 비교 실험
 
# ============================================================
# 0. 환경 설정
# ============================================================
import os
import sys
sys.path.insert(0, '/home/aceya/EMS/src')  # src/ems 경로 추가
 
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from tqdm import tqdm
 
# ============================================================
# 1. 데이터 로드
# ============================================================
from ems.db import load_env, fetch_measurements
from pathlib import Path
 
load_env(Path('/home/claude/EMS/.env'))
 
print("DB에서 데이터 로드 중...")
df = fetch_measurements(
    meter_urn='H1.W11',
    measurement='P',
    start_ts='2018-01-01',
    end_ts='2024-01-01',
    resolution='1h'
)
print(f"로드 완료: {len(df)}건")
print(df.head())
print(df.dtypes)
 
# ============================================================
# 2. 피처 엔지니어링
# ============================================================
df = df.sort_values('ts').reset_index(drop=True)
df['ts'] = pd.to_datetime(df['ts'], utc=True)
 
# 시간 피처
df['hour']      = df['ts'].dt.hour
df['dayofweek'] = df['ts'].dt.dayofweek
df['month']     = df['ts'].dt.month
df['season']    = df['month'].map({12:0,1:0,2:0,3:1,4:1,5:1,6:2,7:2,8:2,9:3,10:3,11:3})
 
# sin/cos 인코딩
df['hour_sin']  = np.sin(2 * np.pi * df['hour'] / 24)
df['hour_cos']  = np.cos(2 * np.pi * df['hour'] / 24)
df['dow_sin']   = np.sin(2 * np.pi * df['dayofweek'] / 7)
df['dow_cos']   = np.cos(2 * np.pi * df['dayofweek'] / 7)
df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12)
df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12)
 
# lag 피처
df['lag_1']  = df['value'].shift(1)
df['lag_24'] = df['value'].shift(24)
df['lag_168']= df['value'].shift(168)  # 1주일 전
 
# rolling 피처
df['rolling_mean_24'] = df['value'].shift(1).rolling(24).mean()
df['rolling_std_24']  = df['value'].shift(1).rolling(24).std()
 
df = df.dropna().reset_index(drop=True)
print(f"\n피처 엔지니어링 완료: {len(df)}건")
print(df.columns.tolist())
 
# ============================================================
# 3. 시간 기준 분할 (80% 학습 / 10% 검증 / 10% 테스트)
# ============================================================
n = len(df)
train_end = int(n * 0.8)
val_end   = int(n * 0.9)
 
train = df.iloc[:train_end]
val   = df.iloc[train_end:val_end]
test  = df.iloc[val_end:]
 
print(f"\n학습: {len(train)}건 ({train['ts'].min()} ~ {train['ts'].max()})")
print(f"검증: {len(val)}건 ({val['ts'].min()} ~ {val['ts'].max()})")
print(f"테스트: {len(test)}건 ({test['ts'].min()} ~ {test['ts'].max()})")
 
# ============================================================
# 4. 피처/타겟 정의
# ============================================================
feature_cols = [
    'hour_sin','hour_cos','dow_sin','dow_cos','month_sin','month_cos',
    'season','lag_1','lag_24','lag_168','rolling_mean_24','rolling_std_24'
]
target_col = 'value'
 
X_train = train[feature_cols].values
y_train = train[target_col].values
X_val   = val[feature_cols].values
y_val   = val[target_col].values
X_test  = test[feature_cols].values
y_test  = test[target_col].values
 
# ============================================================
# 5. 모델 1 - XGBoost
# ============================================================
print("\n[XGBoost] 학습 중...")
from xgboost import XGBRegressor
 
xgb = XGBRegressor(
    n_estimators=300,
    max_depth=6,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    random_state=42,
    n_jobs=-1
)
xgb.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=100)
 
y_pred_xgb = xgb.predict(X_test)
rmse_xgb = np.sqrt(mean_squared_error(y_test, y_pred_xgb))
mae_xgb  = mean_absolute_error(y_test, y_pred_xgb)
r2_xgb   = r2_score(y_test, y_pred_xgb)
print(f"XGBoost - RMSE: {rmse_xgb:.2f}, MAE: {mae_xgb:.2f}, R²: {r2_xgb:.4f}")
 
# ============================================================
# 6. 모델 2 - Prophet
# ============================================================
print("\n[Prophet] 학습 중...")
from prophet import Prophet
 
df_prophet = train[['ts','value']].copy()
df_prophet.columns = ['ds','y']
df_prophet['ds'] = df_prophet['ds'].dt.tz_localize(None)
 
m = Prophet(
    yearly_seasonality=True,
    weekly_seasonality=True,
    daily_seasonality=True,
    changepoint_prior_scale=0.05
)
m.fit(df_prophet)
 
future = pd.DataFrame({'ds': test['ts'].dt.tz_localize(None)})
forecast = m.predict(future)
y_pred_prophet = forecast['yhat'].values
 
rmse_prophet = np.sqrt(mean_squared_error(y_test, y_pred_prophet))
mae_prophet  = mean_absolute_error(y_test, y_pred_prophet)
r2_prophet   = r2_score(y_test, y_pred_prophet)
print(f"Prophet - RMSE: {rmse_prophet:.2f}, MAE: {mae_prophet:.2f}, R²: {r2_prophet:.4f}")
 
# ============================================================
# 7. 모델 3 - LSTM
# ============================================================
print("\n[LSTM] 학습 중...")
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
 
scaler_x = MinMaxScaler()
scaler_y = MinMaxScaler()
 
X_train_s = scaler_x.fit_transform(X_train)
X_val_s   = scaler_x.transform(X_val)
X_test_s  = scaler_x.transform(X_test)
y_train_s = scaler_y.fit_transform(y_train.reshape(-1,1))
y_val_s   = scaler_y.transform(y_val.reshape(-1,1))
 
SEQ_LEN = 24
 
def make_sequences(X, y, seq_len):
    Xs, ys = [], []
    for i in range(len(X) - seq_len):
        Xs.append(X[i:i+seq_len])
        ys.append(y[i+seq_len])
    return np.array(Xs), np.array(ys)
 
X_tr_seq, y_tr_seq = make_sequences(X_train_s, y_train_s, SEQ_LEN)
X_vl_seq, y_vl_seq = make_sequences(X_val_s,   y_val_s,   SEQ_LEN)
X_ts_seq, _        = make_sequences(X_test_s,  np.zeros(len(X_test_s)), SEQ_LEN)
y_test_lstm = y_test[SEQ_LEN:]
 
tr_ds = TensorDataset(torch.FloatTensor(X_tr_seq), torch.FloatTensor(y_tr_seq))
tr_dl = DataLoader(tr_ds, batch_size=64, shuffle=True)
 
class LSTMModel(nn.Module):
    def __init__(self, input_size, hidden_size=64, num_layers=2):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers,
                            batch_first=True, dropout=0.2)
        self.fc = nn.Linear(hidden_size, 1)
    def forward(self, x):
        out, _ = self.lstm(x)
        return self.fc(out[:, -1, :])
 
device = 'cuda' if torch.cuda.is_available() else 'cpu'
model = LSTMModel(input_size=len(feature_cols)).to(device)
optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
criterion = nn.MSELoss()
 
best_val_loss = float('inf')
patience, counter = 10, 0
 
for epoch in tqdm(range(50), desc="LSTM 학습"):
    model.train()
    for xb, yb in tr_dl:
        xb, yb = xb.to(device), yb.to(device)
        optimizer.zero_grad()
        loss = criterion(model(xb), yb)
        loss.backward()
        optimizer.step()
 
    model.eval()
    with torch.no_grad():
        xv = torch.FloatTensor(X_vl_seq).to(device)
        yv = torch.FloatTensor(y_vl_seq).to(device)
        val_loss = criterion(model(xv), yv).item()
 
    if val_loss < best_val_loss:
        best_val_loss = val_loss
        torch.save(model.state_dict(), '/tmp/best_lstm.pt')
        counter = 0
    else:
        counter += 1
        if counter >= patience:
            tqdm.write(f"  Early stop at epoch {epoch+1}")
            break
 
model.load_state_dict(torch.load('/tmp/best_lstm.pt'))
model.eval()
with torch.no_grad():
    xt = torch.FloatTensor(X_ts_seq).to(device)
    y_pred_lstm_s = model(xt).cpu().numpy()
 
y_pred_lstm = scaler_y.inverse_transform(y_pred_lstm_s).flatten()
 
rmse_lstm = np.sqrt(mean_squared_error(y_test_lstm, y_pred_lstm))
mae_lstm  = mean_absolute_error(y_test_lstm, y_pred_lstm)
r2_lstm   = r2_score(y_test_lstm, y_pred_lstm)
print(f"LSTM - RMSE: {rmse_lstm:.2f}, MAE: {mae_lstm:.2f}, R²: {r2_lstm:.4f}")
 
# ============================================================
# 8. 결과 비교
# ============================================================
print("\n" + "="*50)
print("모델 성능 비교")
print("="*50)
results = pd.DataFrame({
    '모델':  ['XGBoost', 'Prophet', 'LSTM'],
    'RMSE':  [rmse_xgb, rmse_prophet, rmse_lstm],
    'MAE':   [mae_xgb,  mae_prophet,  mae_lstm],
    'R²':    [r2_xgb,   r2_prophet,   r2_lstm]
})
print(results.to_string(index=False))
print(f"\n챔피언: {results.loc[results['RMSE'].idxmin(), '모델']}")
 
# ============================================================
# 9. 시각화 (테스트 구간 2주 샘플)
# ============================================================
fig, axes = plt.subplots(3, 1, figsize=(14, 12))
sample = 336  # 2주
 
for ax, (name, y_pred, y_true) in zip(axes, [
    ('XGBoost', y_pred_xgb[:sample],    y_test[:sample]),
    ('Prophet', y_pred_prophet[:sample], y_test[:sample]),
    ('LSTM',    y_pred_lstm[:sample],    y_test_lstm[:sample]),
]):
    ax.plot(y_true, label='실제값', color='#2E75B6', linewidth=1.2)
    ax.plot(y_pred, label=f'{name} 예측', color='#FF6B35',
            linewidth=1.2, linestyle='--', alpha=0.8)
    ax.set_title(f'{name} 예측 vs 실제 (테스트 2주)', fontsize=12)
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.set_ylabel('P (W)')
 
plt.tight_layout()
plt.savefig('/tmp/forecast_comparison.png', dpi=150, bbox_inches='tight')
print("\n시각화 저장 완료: /tmp/forecast_comparison.png")
 