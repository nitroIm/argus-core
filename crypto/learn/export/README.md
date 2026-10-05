# ARGUS ML - Export v2

## Файлы
- `lgb_model.txt` - LightGBM модель
- `model_meta.json` - метрики + features
- `features_hourly.csv` - основные данные
- `external_market.csv` - DXY/SPX/GOLD
- `README.md`

## Как использовать
1. pip install lightgbm==4.5.0
2. model = lgb.Booster(model_file='lgb_model.txt')
3. Объединить features + external по timestamp
4. Признаки (25, порядок важен):
change_pct, range_pct, body_pct, upper_wick_pct, lower_wick_pct, volume_ratio_24h, volatility_24h, volatility_7d, change_4h, change_24h, change_7d, change_1d, change_3d, trend_up, hour_of_day, day_of_week, funding_rate, funding_trend, oi_change_pct, ls_ratio, taker_ratio, ema9_dist_pct, ema21_dist_pct, ema50_dist_pct, macd, macd_signal, bb_upper_dist, bb_lower_dist, bb_width_pct, dist_high_24h_pct, dist_low_24h_pct, consecutive_up, session, ref_change_1h, ref_change_4h, ref_change_24h, ratio, ratio_zscore_24h, lead_lag_corr_24h, spread_pct

5. Target: next_direction (0/1)

## Заметка
Модель переносима. Обе таблицы обязательны.