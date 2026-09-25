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
change_pct, range_pct, body_pct, upper_wick_pct, lower_wick_pct, volume_ratio_24h, volatility_24h, volatility_7d, change_4h, change_24h, change_7d, change_1d, change_3d, trend_up, hour_of_day, day_of_week, funding_rate, funding_trend, oi_change_pct, ls_ratio, taker_ratio, dxy_change_pct, spx_change_pct, gold_change_pct, eth_btc_ratio

5. Target: next_direction (0/1)

## Заметка
Модель переносима. Обе таблицы обязательны.