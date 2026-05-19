from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

sns.set_theme(style='whitegrid', context='talk')

ROOT = Path('/workspace/ems_team_experiment/outputs/meter_forecast_lstm')
FIG_ROOT = Path('/workspace/ems_team_experiment/outputs/figures')
stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
OUT = FIG_ROOT / f'completed_meter_forecast_snapshot_{stamp}'
OUT.mkdir(parents=True, exist_ok=True)

rows = []
for metrics_path in sorted(ROOT.glob('**/metrics.json')):
    try:
        data = json.loads(metrics_path.read_text())
    except Exception as exc:
        rows.append({'path': str(metrics_path), 'status': f'read_error:{exc}'})
        continue
    status = data.get('status')
    meter = data.get('meter_urn') or metrics_path.parent.name
    run_dir = metrics_path.parent
    run_group = run_dir.parent.name
    source = 'full_chunk' if run_group.startswith('full_p_meters_chunk') else ('subset' if run_group.startswith('meter_subset') else run_group)
    rows.append({
        'meter_urn': meter,
        'run_group': run_group,
        'source': source,
        'run_dir': str(run_dir),
        'status': status,
        'val_MAE': (data.get('val') or {}).get('MAE'),
        'val_RMSE': (data.get('val') or {}).get('RMSE'),
        'val_MAPE': (data.get('val') or {}).get('MAPE'),
        'test_MAE': (data.get('test') or {}).get('MAE'),
        'test_RMSE': (data.get('test') or {}).get('RMSE'),
        'test_MAPE': (data.get('test') or {}).get('MAPE'),
        'train_sequences': data.get('train_sequences'),
        'validation_sequences': data.get('validation_sequences'),
        'test_sequences': data.get('test_sequences'),
        'observed_rows': data.get('observed_rows'),
        'missing_before_fill': data.get('missing_before_fill'),
        'fit_rows': data.get('fit_rows'),
        'train_time_sec': data.get('train_time_sec'),
        'device': data.get('device'),
        'mlflow_run_id': data.get('mlflow_run_id'),
    })

all_df = pd.DataFrame(rows)
all_df.to_csv(OUT / 'all_metrics_raw_snapshot.csv', index=False)

completed = all_df[all_df['status'].eq('completed')].copy()
# Prefer full chunk output over subset for duplicated meters; otherwise keep the latest available completed result.
completed['source_priority'] = np.where(completed['source'].eq('full_chunk'), 0, 1)
completed = completed.sort_values(['meter_urn', 'source_priority', 'run_group']).drop_duplicates('meter_urn', keep='first')
completed = completed.sort_values('test_RMSE', na_position='last').reset_index(drop=True)
completed.to_csv(OUT / 'completed_metrics_dedup.csv', index=False)

summary = {
    'snapshot_utc': stamp,
    'metrics_json_total': int(len(all_df)),
    'completed_rows_raw': int((all_df['status'] == 'completed').sum()) if len(all_df) else 0,
    'completed_unique_meters': int(len(completed)),
    'full_chunk_completed_unique_meters': int(completed['source'].eq('full_chunk').sum()) if len(completed) else 0,
    'subset_only_completed_unique_meters': int(completed['source'].eq('subset').sum()) if len(completed) else 0,
}
(OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))

if completed.empty:
    print(json.dumps({'out_dir': str(OUT), **summary}, ensure_ascii=False, indent=2))
    raise SystemExit(0)

# 1) Completed meter metric bars
plot_df = completed.sort_values('test_MAE', ascending=True).copy()
height = max(8, min(28, 0.33 * len(plot_df) + 3))
fig, ax = plt.subplots(figsize=(14, height))
colors = np.where(plot_df['source'].eq('full_chunk'), '#4C78A8', '#F58518')
ax.barh(plot_df['meter_urn'], plot_df['test_MAE'], color=colors)
ax.set_xscale('symlog', linthresh=1000)
ax.set_xlabel('Test MAE, symlog scale')
ax.set_ylabel('Meter')
ax.set_title(f'Completed meter forecasts — Test MAE snapshot (n={len(plot_df)})')
for label, color in [('full_chunk', '#4C78A8'), ('subset_only', '#F58518')]:
    ax.barh([], [], color=color, label=label)
ax.legend(loc='lower right')
fig.tight_layout()
fig.savefig(OUT / '01_completed_test_mae_bar.png', dpi=180)
plt.close(fig)

# 2) MAE/RMSE scatter
fig, ax = plt.subplots(figsize=(12, 8))
scatter = ax.scatter(
    completed['test_MAE'], completed['test_RMSE'],
    c=np.log10(completed['test_MAPE'].clip(lower=1).astype(float)),
    s=np.clip(completed['observed_rows'].fillna(0) / 250, 40, 260),
    cmap='viridis', alpha=0.82, edgecolor='white', linewidth=0.5,
)
ax.set_xscale('symlog', linthresh=1000)
ax.set_yscale('symlog', linthresh=1000)
ax.set_xlabel('Test MAE, symlog scale')
ax.set_ylabel('Test RMSE, symlog scale')
ax.set_title('Completed meter forecasts — MAE vs RMSE')
cbar = fig.colorbar(scatter, ax=ax)
cbar.set_label('log10(Test MAPE clipped >= 1)')
label_df = pd.concat([
    completed.nsmallest(3, 'test_RMSE'),
    completed.nlargest(3, 'test_RMSE'),
]).drop_duplicates('meter_urn')
for _, r in label_df.iterrows():
    ax.annotate(r['meter_urn'], (r['test_MAE'], r['test_RMSE']), xytext=(5, 5), textcoords='offset points', fontsize=10)
fig.tight_layout()
fig.savefig(OUT / '02_completed_metric_scatter.png', dpi=180)
plt.close(fig)

# 3) Validation vs test metric comparison
long = completed.melt(
    id_vars=['meter_urn', 'source'],
    value_vars=['val_MAE', 'test_MAE', 'val_RMSE', 'test_RMSE'],
    var_name='metric_split', value_name='value'
).dropna()
long['metric'] = long['metric_split'].str.extract('(MAE|RMSE)')
long['split'] = np.where(long['metric_split'].str.startswith('val_'), 'validation', 'test')
fig, axes = plt.subplots(1, 2, figsize=(16, 7), sharey=False)
for ax, metric in zip(axes, ['MAE', 'RMSE']):
    sub = long[long['metric'].eq(metric)]
    sns.boxplot(data=sub, x='split', y='value', ax=ax, color='#D7E8F7')
    sns.stripplot(data=sub, x='split', y='value', ax=ax, color='#4C78A8', alpha=0.55, size=5)
    ax.set_yscale('symlog', linthresh=1000)
    ax.set_title(f'{metric}: validation vs test')
    ax.set_xlabel('')
    ax.set_ylabel(metric)
fig.suptitle(f'Completed meter forecasts — metric distribution snapshot (n={len(completed)})', y=1.02)
fig.tight_layout()
fig.savefig(OUT / '03_val_test_metric_distribution.png', dpi=180, bbox_inches='tight')
plt.close(fig)

# 4) Time-series overlays for representative completed meters.
# Pick low, quartile, median, high, and force-in common large/subset examples if available.
sorted_df = completed.sort_values('test_RMSE').reset_index(drop=True)
positions = sorted(set([0, max(0, len(sorted_df)//4), max(0, len(sorted_df)//2), max(0, (3*len(sorted_df))//4), len(sorted_df)-1]))
selected = list(sorted_df.iloc[positions]['meter_urn'])
for m in ['V.Z81', 'V.Z82', 'H2.Z64', 'H3.Z43']:
    if m in set(completed['meter_urn']) and m not in selected:
        selected.append(m)
selected = selected[:8]

n = len(selected)
fig, axes = plt.subplots(n, 1, figsize=(18, max(4*n, 8)), sharex=False)
if n == 1:
    axes = [axes]
for ax, meter in zip(axes, selected):
    rec = completed[completed['meter_urn'].eq(meter)].iloc[0]
    pred_path = Path(rec['run_dir']) / 'predictions_test.parquet'
    try:
        pred = pd.read_parquet(pred_path).sort_values('ts')
        # Show first 14 days with a valid test timestamp for readability.
        start = pred['ts'].min()
        end = start + pd.Timedelta(days=14)
        window = pred[pred['ts'].between(start, end)].copy()
        ax.plot(window['ts'], window['y_true'], label='actual', linewidth=1.5, color='#222222')
        ax.plot(window['ts'], window['y_pred'], label='predicted', linewidth=1.3, color='#E45756', alpha=0.85)
        ax.set_title(f"{meter} — test first 14 days | MAE={rec['test_MAE']:.1f}, RMSE={rec['test_RMSE']:.1f}, source={rec['source']}")
        ax.set_ylabel('P')
        ax.legend(loc='upper right', ncol=2, fontsize=10)
    except Exception as exc:
        ax.text(0.02, 0.5, f'{meter}: failed to read predictions: {exc}', transform=ax.transAxes)
fig.suptitle('Completed meter forecasts — actual vs predicted test time series examples', y=0.995)
fig.tight_layout()
fig.savefig(OUT / '04_timeseries_actual_vs_pred_examples.png', dpi=180, bbox_inches='tight')
plt.close(fig)

# 5) Actual-vs-predicted scatter for selected meters, sampled for readability.
fig, axes = plt.subplots(2, int(np.ceil(n/2)), figsize=(18, 9))
axes = np.ravel(axes)
for ax in axes[n:]:
    ax.axis('off')
for ax, meter in zip(axes, selected):
    rec = completed[completed['meter_urn'].eq(meter)].iloc[0]
    pred_path = Path(rec['run_dir']) / 'predictions_test.parquet'
    pred = pd.read_parquet(pred_path)
    if len(pred) > 1500:
        pred = pred.sample(1500, random_state=42)
    ax.scatter(pred['y_true'], pred['y_pred'], s=8, alpha=0.35, color='#4C78A8')
    lo = float(np.nanmin([pred['y_true'].min(), pred['y_pred'].min()]))
    hi = float(np.nanmax([pred['y_true'].max(), pred['y_pred'].max()]))
    ax.plot([lo, hi], [lo, hi], color='#E45756', linewidth=1)
    ax.set_title(meter, fontsize=12)
    ax.set_xlabel('actual P')
    ax.set_ylabel('predicted P')
fig.suptitle('Completed meter forecasts — actual vs predicted scatter examples', y=1.02)
fig.tight_layout()
fig.savefig(OUT / '05_actual_pred_scatter_examples.png', dpi=180, bbox_inches='tight')
plt.close(fig)

# 6) Loss curves for selected meters where available.
fig, ax = plt.subplots(figsize=(12, 7))
for meter in selected:
    rec = completed[completed['meter_urn'].eq(meter)].iloc[0]
    loss_path = Path(rec['run_dir']) / 'loss_history.csv'
    if not loss_path.exists():
        continue
    loss = pd.read_csv(loss_path)
    if 'epoch' not in loss.columns:
        loss['epoch'] = np.arange(1, len(loss) + 1)
    y_col = 'val_loss' if 'val_loss' in loss.columns else loss.columns[-1]
    ax.plot(loss['epoch'], loss[y_col], marker='o', linewidth=1.2, label=meter)
ax.set_xlabel('Epoch')
ax.set_ylabel('Validation loss')
ax.set_title('Completed meter forecasts — validation loss curves for selected examples')
ax.legend(ncol=2, fontsize=9)
fig.tight_layout()
fig.savefig(OUT / '06_selected_loss_curves.png', dpi=180)
plt.close(fig)

print(json.dumps({'out_dir': str(OUT), 'selected_meters': selected, **summary}, ensure_ascii=False, indent=2))
