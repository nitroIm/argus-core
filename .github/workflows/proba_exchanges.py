name: Crypto - Probe Exchanges

on:
  workflow_dispatch:
  # Раскомментируй, если хочешь раз в неделю проверять, что API не меняются
  # schedule:
  #   - cron: '0 3 * * 1'

permissions:
  contents: write

jobs:
  probe:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install deps
        run: |
          python -m pip install --upgrade pip
          pip install requests

      - name: Run probe
        run: |
          python crypto/probe_exchanges.py

      - name: Show summary
        if: always()
        run: |
          echo "=== Отчёт ==="
          if [ -f crypto/data/exchange_probe.json ]; then
            python - <<'EOF'
import json
data = json.load(open("crypto/data/exchange_probe.json"))
s = data.get("summary", {})
print(f"Всего метрик: {s.get('total_metrics_available', 0)}")
print(f"Объём за месяц: ~{s.get('total_mb_per_month_all', 0)} МБ")
EOF
          else
            echo "❌ Отчёт не создан"
          fi

      - name: Commit report
        if: always()
        run: |
          git config user.name "ARGUS"
          git config user.email "argus@github.com"
          git add crypto/data/exchange_probe.json || true
          if git diff --staged --quiet; then
            echo "Нет изменений для коммита"
          else
            git commit -m "crypto: probe exchanges $(date -u +%Y-%m-%dT%H:%M:%SZ)"
            git pull --rebase origin main || echo "rebase failed, continuing"
            git push origin main || echo "push failed"
          fi