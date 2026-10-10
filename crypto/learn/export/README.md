# ARGUS ML - Export v8

## Per-symbol models
- lgb_BTCUSDT.txt + meta_BTCUSDT.json
- lgb_ETHUSDT.txt + meta_ETHUSDT.json
- lgb_SOLUSDT.txt + meta_SOLUSDT.json
- lgb_BNBUSDT.txt + meta_BNBUSDT.json

## Compat
- lgb_model.txt = copy of BTC
- model_meta.json = copy of BTC meta

## Data
- features_hourly.csv - 30 internal cols

## Config
- HORIZON: 12h
- REFERENCE: BTCUSDT
- FEATURE_COLS: 40
- USE_CROSS: 0 (off)

## Usage
1. pip install lightgbm==4.5.0
2. model = lgb.Booster(
     model_file='lgb_BTCUSDT.txt')