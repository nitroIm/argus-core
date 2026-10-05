# ARGUS ML - Export v4

## Per-symbol модели
- `lgb_BTCUSDT.txt` + `meta_BTCUSDT.json`
- `lgb_ETHUSDT.txt` + `meta_ETHUSDT.json`
- `lgb_SOLUSDT.txt` + `meta_SOLUSDT.json`
- `lgb_BNBUSDT.txt` + `meta_BNBUSDT.json`

## Совместимость
- `lgb_model.txt` = копия BTC-модели
- `model_meta.json` = копия meta BTC

## Данные
- `features_hourly.csv` - внутренние фичи
- `external_market.csv` - DXY/SPX/GOLD

## Параметры
- HORIZON: 12h
- THRESHOLD: 0.5%
- REFERENCE: BTCUSDT
- FEATURE_COLS: 40

## Использование
1. pip install lightgbm==4.5.0
2. model = lgb.Booster(
     model_file='lgb_BTCUSDT.txt')