#!/usr/bin/env python3
"""Batch meter-level LSTM forecasts for EMS P series.

Paper-aligned meter forecast:
- one independent model per meter
- target: next-hour signed meter P
- default features: meter_P + calendar sin/cos
- preprocessing: missing=0, MinMaxScaler(0..1, clip=True), fit on train non-outage rows
- MLflow experiment: SSA-IPSO-LSTM
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psycopg
import torch
import torch.nn as nn
from dotenv import load_dotenv
from sklearn.preprocessing import MinMaxScaler
from torch.utils.data import DataLoader, TensorDataset

PROJECT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_DIR / ".env"
OUT_BASE = PROJECT_DIR / "outputs" / "meter_forecast_lstm"
CALENDAR_COLS = ["hour_sin", "hour_cos", "dow_sin", "dow_cos", "month_sin", "month_cos"]
GATEWAY_OUTAGES = [
    ("2020-02-13 00:00:00+00", "2020-03-06 00:00:00+00"),
    ("2020-08-20 00:00:00+00", "2020-09-17 00:00:00+00"),
    ("2021-11-15 00:00:00+00", "2021-12-10 00:00:00+00"),
    ("2022-05-06 00:00:00+00", "2022-07-14 00:00:00+00"),
]
DEFAULT_METERS = ["H1.Z16", "H1.Z11", "H1.Z12", "H1.Z24", "H1.Z25", "V.Z84", "H1.Z310", "H2.Z311", "H3.Z312", "H1.Z20", "V.Z81", "V.Z82", "H2.Z351", "H2.Z361", "H3.Z43", "H3.Z44", "H2.Z64", "H2.Z65"]


class LSTMRegressor(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, num_layers: int, dropout: float) -> None:
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, num_layers, batch_first=True, dropout=dropout if num_layers > 1 else 0.0)
        self.head = nn.Linear(hidden_size, 1)
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x)
        return self.head(out[:, -1, :]).squeeze(-1)


def connect():
    load_dotenv(ENV_PATH)
    return psycopg.connect(host=os.environ["DB_HOST"], port=os.environ["DB_PORT"], dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], connect_timeout=10)


def load_meter_p(meter: str) -> pd.DataFrame:
    sql = """
    SELECT ts, value AS "meter_P"
    FROM ems.cr_measurement_1h
    WHERE meter_urn = %s AND measurement = 'P'
      AND ts >= '2018-01-01 00:00:00+00'::timestamptz
      AND ts <  '2024-01-01 00:00:00+00'::timestamptz
    ORDER BY ts
    """
    with connect() as conn:
        return pd.read_sql(sql, conn, params=(meter,))


def add_time_columns(df: pd.DataFrame) -> pd.DataFrame:
    ts = pd.to_datetime(df["ts"], utc=True)
    df["split"] = np.select([
        (ts >= pd.Timestamp("2018-01-01", tz="UTC")) & (ts < pd.Timestamp("2022-01-01", tz="UTC")),
        (ts >= pd.Timestamp("2022-01-01", tz="UTC")) & (ts < pd.Timestamp("2023-01-01", tz="UTC")),
        (ts >= pd.Timestamp("2023-01-01", tz="UTC")) & (ts < pd.Timestamp("2024-01-01", tz="UTC")),
    ], ["train", "validation", "test"], default="exclude")
    outage = pd.Series(False, index=df.index)
    for start_s, end_s in GATEWAY_OUTAGES:
        outage |= (ts >= pd.Timestamp(start_s)) & (ts < pd.Timestamp(end_s))
    df["is_gateway_outage"] = outage
    hour, dow, month0 = ts.dt.hour, ts.dt.dayofweek, ts.dt.month - 1
    df["hour_sin"] = np.sin(2 * math.pi * hour / 24); df["hour_cos"] = np.cos(2 * math.pi * hour / 24)
    df["dow_sin"] = np.sin(2 * math.pi * dow / 7); df["dow_cos"] = np.cos(2 * math.pi * dow / 7)
    df["month_sin"] = np.sin(2 * math.pi * month0 / 12); df["month_cos"] = np.cos(2 * math.pi * month0 / 12)
    return df


def prepare_meter_df(meter: str) -> tuple[pd.DataFrame, MinMaxScaler, list[str], dict]:
    long = load_meter_p(meter)
    full_index = pd.date_range("2018-01-01 00:00:00+00:00", "2024-01-01 00:00:00+00:00", freq="1h", inclusive="left")
    df = long.set_index("ts").reindex(full_index).rename_axis("ts").reset_index()
    df["meter_P_observed"] = df["meter_P"].notna()
    df = add_time_columns(df)
    missing_before = int(df["meter_P"].isna().sum())
    df["meter_P"] = df["meter_P"].fillna(0.0)
    feature_cols = ["meter_P"] + CALENDAR_COLS
    scaler = MinMaxScaler(feature_range=(0, 1), clip=True)
    fit_mask = (df["split"] == "train") & (~df["is_gateway_outage"])
    scaler.fit(df.loc[fit_mask, feature_cols])
    df[feature_cols] = scaler.transform(df[feature_cols]).astype("float32")
    meta = {"missing_before_fill": missing_before, "observed_rows": int(df["meter_P_observed"].sum()), "fit_rows": int(fit_mask.sum())}
    return df, scaler, feature_cols, meta


def make_sequences(df: pd.DataFrame, feature_cols: list[str], seq_len: int, horizon: int):
    values = df[feature_cols].to_numpy(dtype=np.float32)
    y = df["meter_P"].to_numpy(dtype=np.float32)
    splits = df["split"].to_numpy(); outage = df["is_gateway_outage"].to_numpy(dtype=bool); obs = df["meter_P_observed"].to_numpy(dtype=bool); ts = df["ts"].to_numpy()
    xs=[]; ys=[]; out_split=[]; out_ts=[]; valid_train=[]
    for y_idx in range(seq_len + horizon - 1, len(df)):
        x_start = y_idx - horizon - seq_len + 1; x_end = y_idx - horizon + 1
        if len(set(splits[x_start:x_end].tolist() + [splits[y_idx]])) != 1: continue
        if not obs[y_idx]: continue
        xs.append(values[x_start:x_end]); ys.append(y[y_idx]); out_split.append(splits[y_idx]); out_ts.append(ts[y_idx])
        valid_train.append(bool(splits[y_idx] == "train" and not outage[x_start:y_idx+1].any()))
    return np.stack(xs).astype(np.float32), np.array(ys,dtype=np.float32), np.array(out_split), np.array(out_ts), np.array(valid_train,dtype=bool)


def inverse_meter(scaled: np.ndarray, scaler: MinMaxScaler) -> np.ndarray:
    return scaled * (float(scaler.data_max_[0]) - float(scaler.data_min_[0])) + float(scaler.data_min_[0])

def calc_metrics(y_true, y_pred):
    err=y_pred-y_true
    return {"MAE": float(np.mean(np.abs(err))), "RMSE": float(np.sqrt(np.mean(err**2))), "MAPE": float(np.mean(np.abs(err)/np.maximum(np.abs(y_true),1.0))*100)}


def train_one(meter: str, args, device, mlflow_client_enabled: bool) -> dict:
    safe = meter.replace('.', '_')
    out_dir = OUT_BASE / args.run_name / safe
    out_dir.mkdir(parents=True, exist_ok=True)
    df, scaler, feature_cols, meta = prepare_meter_df(meter)
    x,y,splits,ts,valid_train = make_sequences(df, feature_cols, args.seq_len, args.horizon)
    train_mask=(splits=="train") & valid_train; val_mask=splits=="validation"; test_mask=splits=="test"
    if train_mask.sum() < args.min_train_sequences or val_mask.sum()==0 or test_mask.sum()==0:
        res={"meter_urn": meter, "status": "skipped", "reason": "insufficient_sequences", "train_sequences": int(train_mask.sum()), "validation_sequences": int(val_mask.sum()), "test_sequences": int(test_mask.sum()), **meta}
        (out_dir/"metrics.json").write_text(json.dumps(res,ensure_ascii=False,indent=2)); return res
    model=LSTMRegressor(len(feature_cols), args.hidden_size, args.num_layers, args.dropout).to(device)
    opt=torch.optim.AdamW(model.parameters(), lr=args.lr); crit=nn.MSELoss()
    loader=DataLoader(TensorDataset(torch.from_numpy(x[train_mask]), torch.from_numpy(y[train_mask])), batch_size=args.batch_size, shuffle=True)
    val_x=torch.from_numpy(x[val_mask]).to(device); val_y=torch.from_numpy(y[val_mask]).to(device)
    best=float('inf'); state=None; patience=args.patience; hist=[]; t0=time.time()
    for epoch in range(1,args.epochs+1):
        model.train(); losses=[]
        for xb,yb in loader:
            xb=xb.to(device); yb=yb.to(device); opt.zero_grad(set_to_none=True); loss=crit(model(xb),yb); loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(),1.0); opt.step(); losses.append(float(loss.detach().cpu()))
        model.eval()
        with torch.no_grad(): val_loss=float(crit(model(val_x),val_y).detach().cpu())
        hist.append({"epoch":epoch,"train_loss":float(np.mean(losses)),"val_loss":val_loss})
        if val_loss < best: best=val_loss; state={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}; patience=args.patience
        else:
            patience-=1
            if patience<=0: break
    if state: model.load_state_dict(state)
    model.eval()
    def pred(mask):
        outs=[]
        for xb in DataLoader(torch.from_numpy(x[mask]), batch_size=args.batch_size):
            with torch.no_grad(): outs.append(model(xb.to(device)).detach().cpu().numpy())
        return np.concatenate(outs)
    val_pred=inverse_meter(pred(val_mask), scaler); val_true=inverse_meter(y[val_mask], scaler)
    test_pred=inverse_meter(pred(test_mask), scaler); test_true=inverse_meter(y[test_mask], scaler)
    res={"meter_urn":meter,"status":"completed","val":calc_metrics(val_true,val_pred),"test":calc_metrics(test_true,test_pred),"train_sequences":int(train_mask.sum()),"validation_sequences":int(val_mask.sum()),"test_sequences":int(test_mask.sum()),"train_time_sec":time.time()-t0,"device":str(device),**meta}
    pd.DataFrame(hist).to_csv(out_dir/"loss_history.csv",index=False)
    pd.DataFrame({"ts":ts[test_mask],"y_true":test_true,"y_pred":test_pred}).to_parquet(out_dir/"predictions_test.parquet",index=False)
    torch.save({"model_state_dict":model.state_dict(),"params":vars(args),"feature_cols":feature_cols,"meter_urn":meter}, out_dir/"model.pt")
    joblib.dump(scaler, out_dir/"scaler.pkl")
    (out_dir/"metrics.json").write_text(json.dumps(res,ensure_ascii=False,indent=2), encoding="utf-8")
    if mlflow_client_enabled:
        import mlflow
        with mlflow.start_run(run_name=f"meter_{safe}_{args.run_name}", nested=False):
            mlflow.log_params({"target_scope":"meter_P", "meter_urn":meter, "target":"next_hour_signed_meter_P", "features":",".join(feature_cols), "resolution":"1h", "source_relation":"ems.cr_measurement_1h", "scaler":"MinMaxScaler(0,1,clip=True)", "missing":"fill_0", "seq_len":args.seq_len, "horizon":args.horizon, "hidden_size":args.hidden_size, "num_layers":args.num_layers, "epochs":args.epochs})
            for split in ["val","test"]:
                for k,v in res[split].items(): mlflow.log_metric(f"{split}_{k}", v)
            mlflow.log_metric("train_sequences", res["train_sequences"]); mlflow.log_metric("train_time_sec", res["train_time_sec"])
            for fn in ["metrics.json","loss_history.csv","predictions_test.parquet","model.pt"]: mlflow.log_artifact(str(out_dir/fn), artifact_path=f"meter_forecast/{safe}")
            res["mlflow_run_id"] = mlflow.active_run().info.run_id
            (out_dir/"metrics.json").write_text(json.dumps(res,ensure_ascii=False,indent=2), encoding="utf-8")
    return res


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--meters", default=",".join(DEFAULT_METERS))
    ap.add_argument("--seq-len", type=int, default=24); ap.add_argument("--horizon", type=int, default=1)
    ap.add_argument("--hidden-size", type=int, default=32); ap.add_argument("--num-layers", type=int, default=1); ap.add_argument("--dropout", type=float, default=0.0)
    ap.add_argument("--lr", type=float, default=1e-3); ap.add_argument("--batch-size", type=int, default=256); ap.add_argument("--epochs", type=int, default=10); ap.add_argument("--patience", type=int, default=5)
    ap.add_argument("--run-name", default="meter_subset_seq24")
    ap.add_argument("--mlflow-uri", default=""); ap.add_argument("--mlflow-experiment", default="SSA-IPSO-LSTM")
    ap.add_argument("--min-train-sequences", type=int, default=1000)
    args=ap.parse_args()
    meters=[m.strip() for m in args.meters.split(',') if m.strip()]
    OUT_BASE.mkdir(parents=True, exist_ok=True)
    mlflow_enabled=bool(args.mlflow_uri)
    if mlflow_enabled:
        import mlflow
        mlflow.set_tracking_uri(args.mlflow_uri); mlflow.set_experiment(args.mlflow_experiment)
    device=torch.device("cuda" if torch.cuda.is_available() else "cpu")
    results=[]
    for i,m in enumerate(meters,1):
        print(f"[{i}/{len(meters)}] meter={m}", flush=True)
        try: results.append(train_one(m,args,device,mlflow_enabled))
        except Exception as e:
            res={"meter_urn":m,"status":"error","error":type(e).__name__,"message":str(e)}; results.append(res); print(res, flush=True)
    summary=pd.json_normalize(results)
    out_dir=OUT_BASE/args.run_name; out_dir.mkdir(parents=True,exist_ok=True)
    summary.to_csv(out_dir/"meter_metrics.csv", index=False)
    (out_dir/"summary.json").write_text(json.dumps(results,ensure_ascii=False,indent=2), encoding="utf-8")
    print("meter_batch_done")
    print(summary.to_string(index=False))

if __name__ == "__main__":
    main()
