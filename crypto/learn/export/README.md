# ARGUS ML - Export v3

## Файлы
- `lgb_model.txt` - LightGBM модель
- `model_meta.json` - метрики + features
- `features_hourly.csv` - внутренние фичи
- `external_market.csv` - DXY/SPX/GOLD
- `README.md`

## Модель
- HORIZON: 4h
- THRESHOLD: 0.5%
- REFERENCE: BTCUSDT
- FEATURE_COLS (полный набор): 40

## Как использовать
1. pip install lightgbm==4.5.0
2. model = lgb.Booster(model_file='lgb_model.txt')

3. Внутренние фичи (33):
change_pct, range_pct, body_pct, upper_wick_pct, lower_wick_pct, volume_ratio_24h, volatility_24h, volatility_7d, change_4h, change_24h, change_7d, change_1d, change_3d, trend_up, hour_of_day, day_of_week, funding_rate, funding_trend, oi_change_pct, ls_ratio, taker_ratio, ema9_dist_pct, ema21_dist_pct, ema50_dist_pct, macd, macd_signal, bb_upper_dist, bb_lower_dist, bb_width_pct, dist_high_24h_pct, dist_low_24h_pct, consecutive_up, session

4. Кросс-фичи строятся на лету из candles
(см. crypto/learn/dataset.py build_cross_full)

5. Target: next_direction (0/1)
   threshold: 0.5%

## Заметка
features_hourly.csv содержит ТОЛЬКО внутренние фичи.
Cross-features BTC-relative строятся кодом.