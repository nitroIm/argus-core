# ARGUS ML - Export

## Что внутри
- lgb_model.txt - LightGBM модель
- model_meta.json - метрики + список features
- features_hourly.csv - данные для переобучения
- README.md - этот файл

## Как использовать на новой машине

1. Установить зависимости:
pip install lightgbm==4.5.0 scikit-learn==1.5.2 pandas

2. Загрузить модель:
import lightgbm as lgb
model = lgb.Booster(model_file='lgb_model.txt')

3. Признаки (порядок важен):
change_pct, range_pct, body_pct, upper_wick_pct, lower_wick_pct, volume_ratio_24h, volatility_24h, volatility_7d, change_4h, change_24h, change_7d, change_1d, change_3d, trend_up, hour_of_day, day_of_week, funding_rate, funding_trend, oi_change_pct, ls_ratio, taker_ratio

4. Целевая: next_direction (0/1)

5. Переобучение:
python train.py

## Формат CSV
symbol, timestamp, change_pct, range_pct, body_pct, upper_wick_pct, lower_wick_pct, volume_ratio_24h, volatility_24h, volatility_7d, change_4h, change_24h, change_7d, change_1d, change_3d, trend_up, hour_of_day, day_of_week, funding_rate, funding_trend, oi_change_pct, ls_ratio, taker_ratio, next_direction

## Заметка
Модель переносима. Привязок к БД нет.