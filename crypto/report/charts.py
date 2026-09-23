# ============================================================
# ARGUS-Trader — CHARTS v3
# ------------------------------------------------------------
# v3: + EMA24/EMA168 на свечах
#     + plot_rsi (RSI-14)
#     + plot_funding (история funding)
#     + plot_oi (Open Interest)
# v2: fix markov text
# ============================================================

import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

log = logging.getLogger("crypto.charts")

BG = "#111318"
FG = "#e8eaed"
GRID = "#2a2d35"
GREEN = "#26a69a"
RED = "#ef5350"
BLUE = "#42a5f5"
YELLOW = "#ffca28"
ORANGE = "#ffa726"
PURPLE = "#ab47bc"
DARK = "#111318"


def _setup_style():
    plt.rcParams.update({
        "figure.facecolor": BG,
        "axes.facecolor": BG,
        "axes.edgecolor": GRID,
        "axes.labelcolor": FG,
        "xtick.color": FG,
        "ytick.color": FG,
        "text.color": FG,
        "grid.color": GRID,
        "grid.alpha": 0.5,
        "font.size": 10,
    })


_setup_style()


def _ema(values, period):
    """EMA без pandas."""
    if not values:
        return []
    k = 2.0 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def plot_candles(symbol, candles, supports=None,
                 resistances=None,
                 output_path=None, title=None):
    if not candles:
        log.warning("plot_candles: empty")
        return None

    if output_path is None:
        output_path = "/tmp/" + symbol + "_candles.png"

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(10, 6),
        gridspec_kw={"height_ratios": [3, 1]},
        sharex=True,
    )

    xs = np.arange(len(candles))
    closes = [c["close"] for c in candles]

    for i, c in enumerate(candles):
        color = GREEN if c["close"] >= c["open"] else RED
        ax1.plot(
            [i, i], [c["low"], c["high"]],
            color=color, linewidth=0.8, alpha=0.8,
        )
        body_low = min(c["open"], c["close"])
        body_high = max(c["open"], c["close"])
        if body_high == body_low:
            body_high = body_low + 0.0001
        ax1.plot(
            [i, i], [body_low, body_high],
            color=color, linewidth=3,
            solid_capstyle="butt",
        )

    # EMA 24 и 168 (если данных хватает)
    if len(candles) >= 24:
        ema24 = _ema(closes, 24)
        ax1.plot(
            xs, ema24, color=BLUE,
            linewidth=1.5, alpha=0.9,
            label="EMA 24",
        )
    if len(candles) >= 50:
        ema50 = _ema(closes, 50)
        ax1.plot(
            xs, ema50, color=YELLOW,
            linewidth=1.5, alpha=0.7,
            label="EMA 50",
        )
    if len(candles) >= 100:
        ema100 = _ema(closes, 100)
        ax1.plot(
            xs, ema100, color=PURPLE,
            linewidth=1.3, alpha=0.6,
            label="EMA 100",
        )

    if supports:
        for s in supports[:3]:
            ax1.axhline(
                y=s["price"], color=GREEN,
                linestyle="--", linewidth=1, alpha=0.6,
            )
            ax1.text(
                0, s["price"],
                " S " + format(s["price"], ",.0f"),
                color=GREEN, fontsize=8, va="bottom",
            )

    if resistances:
        for r in resistances[:3]:
            ax1.axhline(
                y=r["price"], color=RED,
                linestyle="--", linewidth=1, alpha=0.6,
            )
            ax1.text(
                0, r["price"],
                " R " + format(r["price"], ",.0f"),
                color=RED, fontsize=8, va="top",
            )

    current = closes[-1]
    first = closes[0]
    change_pct = (current - first) / first * 100

    if title is None:
        title = symbol.replace("USDT", "")

    if current >= 100:
        price_str = "$" + format(current, ",.2f")
    else:
        price_str = "$" + format(current, ".4f")

    ch_str = format(change_pct, "+.2f") + "%"

    ax1.set_title(
        title + " - " + price_str + " (" + ch_str + ")",
        color=FG, fontsize=12, fontweight="bold",
    )
    ax1.set_ylabel("Price", color=FG, fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.tick_params(labelsize=9)
    ax1.legend(
        loc="upper left", fontsize=8,
        facecolor=BG, edgecolor=GRID,
        labelcolor=FG,
    )

    for i, c in enumerate(candles):
        color = GREEN if c["close"] >= c["open"] else RED
        ax2.bar(i, c["volume"], color=color,
                alpha=0.6, width=0.7)

    ax2.set_ylabel("Volume", color=FG, fontsize=9)
    ax2.grid(True, alpha=0.3)
    ax2.tick_params(labelsize=9)

    step = max(1, len(candles) // 6)
    ticks = list(range(0, len(candles), step))
    labels = []
    for i in ticks:
        ts = candles[i]["timestamp"]
        labels.append(ts.strftime("%d.%m %H:%M"))

    ax2.set_xticks(ticks)
    ax2.set_xticklabels(labels, rotation=30, ha="right")

    plt.tight_layout()
    plt.savefig(output_path, dpi=90, facecolor=BG)
    plt.close(fig)

    log.info("chart saved: " + output_path)
    return output_path


def plot_pattern(symbol, binary_string, output_path=None):
    if not binary_string:
        log.warning("plot_pattern: empty")
        return None

    if output_path is None:
        output_path = "/tmp/" + symbol + "_pattern.png"

    data = binary_string[-50:] if len(binary_string) >= 50 \
        else binary_string
    xs = np.arange(len(data))
    ys = [1 if c == "1" else -1 for c in data]
    colors = [GREEN if y > 0 else RED for y in ys]

    fig, ax = plt.subplots(figsize=(10, 3))

    ax.bar(xs, ys, color=colors, alpha=0.8, width=0.7)
    ax.axhline(y=0, color=FG, linewidth=0.5, alpha=0.5)

    ups = data.count("1")
    downs = data.count("0")
    ratio = ups / len(data) * 100 if data else 0

    title = symbol.replace("USDT", "")
    ax.set_title(
        title + " - " + str(len(data)) + "h pattern | "
        + "up " + str(ups) + " down " + str(downs)
        + " (" + format(ratio, ".0f") + "% up)",
        color=FG, fontsize=11,
    )
    ax.set_ylabel("Direction", color=FG, fontsize=9)
    ax.set_yticks([-1, 1])
    ax.set_yticklabels(["down", "up"], color=FG)
    ax.set_xlabel("Hours ago -> now", color=FG, fontsize=9)
    ax.grid(True, alpha=0.2, axis="y")
    ax.tick_params(labelsize=8)

    plt.tight_layout()
    plt.savefig(output_path, dpi=90, facecolor=BG)
    plt.close(fig)

    log.info("pattern saved: " + output_path)
    return output_path


def plot_markov(symbol, markov, output_path=None):
    if not markov:
        log.warning("plot_markov: empty")
        return None

    if output_path is None:
        output_path = "/tmp/" + symbol + "_markov.png"

    matrix = np.array([
        [markov.get("p_1_given_0", 0),
         markov.get("p_0_given_0", 0)],
        [markov.get("p_1_given_1", 0),
         markov.get("p_0_given_1", 0)],
    ])

    fig, ax = plt.subplots(figsize=(6, 5))

    im = ax.imshow(matrix, cmap="RdYlGn", vmin=0, vmax=1)

    ax.set_xticks([0, 1])
    ax.set_yticks([0, 1])
    ax.set_xticklabels(["-> UP", "-> DOWN"],
                       color=FG, fontsize=10)
    ax.set_yticklabels(["from DOWN", "from UP"],
                       color=FG, fontsize=10)

    for i in range(2):
        for j in range(2):
            value = matrix[i, j]
            ax.text(
                j, i, format(value, ".2f"),
                ha="center", va="center",
                color=DARK, fontsize=15,
                fontweight="bold",
            )

    title = symbol.replace("USDT", "")
    ax.set_title(
        title + " - Markov transitions",
        color=FG, fontsize=12,
    )

    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.ax.yaxis.set_tick_params(color=FG)
    plt.setp(
        plt.getp(cbar.ax.axes, "yticklabels"),
        color=FG,
    )

    plt.tight_layout()
    plt.savefig(output_path, dpi=90, facecolor=BG)
    plt.close(fig)

    log.info("markov saved: " + output_path)
    return output_path


def compute_rsi(closes, period=14):
    """RSI-14 без pandas."""
    if len(closes) < period + 1:
        return []
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi = [None] * period
    if avg_loss == 0:
        rsi.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi.append(100 - 100 / (1 + rs))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi.append(100 - 100 / (1 + rs))
    return rsi


def plot_rsi(symbol, candles, output_path=None):
    if not candles or len(candles) < 20:
        log.warning("plot_rsi: not enough candles")
        return None

    if output_path is None:
        output_path = "/tmp/" + symbol + "_rsi.png"

    closes = [c["close"] for c in candles]
    rsi = compute_rsi(closes, 14)

    xs = list(range(len(rsi)))

    fig, ax = plt.subplots(figsize=(10, 3))

    ax.plot(xs, rsi, color=BLUE, linewidth=1.5)
    ax.axhline(y=70, color=RED, linestyle="--",
               linewidth=1, alpha=0.6)
    ax.axhline(y=30, color=GREEN, linestyle="--",
               linewidth=1, alpha=0.6)
    ax.axhline(y=50, color=GRID, linestyle=":",
               linewidth=0.8, alpha=0.5)

    ax.fill_between(
        xs, 70, 100, color=RED, alpha=0.1,
    )
    ax.fill_between(
        xs, 0, 30, color=GREEN, alpha=0.1,
    )

    ax.set_ylim(0, 100)
    ax.set_yticks([0, 30, 50, 70, 100])

    current_rsi = rsi[-1] if rsi else 0
    if current_rsi >= 70:
        state = "OVERBOUGHT"
        state_color = RED
    elif current_rsi <= 30:
        state = "OVERSOLD"
        state_color = GREEN
    else:
        state = "neutral"
        state_color = FG

    title = symbol.replace("USDT", "")
    ax.set_title(
        title + " - RSI(14) = "
        + format(current_rsi, ".1f")
        + " (" + state + ")",
        color=state_color, fontsize=11,
    )
    ax.set_ylabel("RSI", color=FG, fontsize=9)
    ax.set_xlabel("Hours ago -> now", color=FG, fontsize=9)
    ax.grid(True, alpha=0.2)
    ax.tick_params(labelsize=8)

    plt.tight_layout()
    plt.savefig(output_path, dpi=90, facecolor=BG)
    plt.close(fig)

    log.info("rsi saved: " + output_path)
    return output_path


def plot_funding(symbol, funding_data, output_path=None):
    """
    funding_data: список dict
    [{"timestamp": dt, "rate": float}, ...]
    rate в СЫРОМ виде (0.0001).
    """
    if not funding_data:
        log.warning("plot_funding: empty")
        return None

    if output_path is None:
        output_path = "/tmp/" + symbol + "_funding.png"

    xs = list(range(len(funding_data)))
    rates = [f["rate"] * 100 for f in funding_data]
    colors = [GREEN if r >= 0 else RED for r in rates]

    fig, ax = plt.subplots(figsize=(10, 3))

    ax.bar(xs, rates, color=colors, alpha=0.8, width=0.7)
    ax.axhline(y=0, color=FG, linewidth=0.5, alpha=0.5)

    current = rates[-1] if rates else 0
    avg = sum(rates) / len(rates) if rates else 0

    if current > 0.01:
        state = "LONGS PAY (bullish crowd)"
        state_color = RED
    elif current < -0.01:
        state = "SHORTS PAY (bearish crowd)"
        state_color = GREEN
    else:
        state = "balanced"
        state_color = FG

    title = symbol.replace("USDT", "")
    ax.set_title(
        title + " - funding | cur "
        + format(current, "+.4f") + "% | avg "
        + format(avg, "+.4f") + "% | " + state,
        color=state_color, fontsize=10,
    )
    ax.set_ylabel("Funding %", color=FG, fontsize=9)
    ax.set_xlabel("Last " + str(len(rates)) + " updates",
                  color=FG, fontsize=9)
    ax.grid(True, alpha=0.2, axis="y")
    ax.tick_params(labelsize=8)

    plt.tight_layout()
    plt.savefig(output_path, dpi=90, facecolor=BG)
    plt.close(fig)

    log.info("funding saved: " + output_path)
    return output_path


def plot_oi(symbol, oi_data, output_path=None):
    """
    oi_data: список dict
    [{"timestamp": dt, "oi": float}, ...]
    """
    if not oi_data:
        log.warning("plot_oi: empty")
        return None

    if output_path is None:
        output_path = "/tmp/" + symbol + "_oi.png"

    xs = list(range(len(oi_data)))
    values = [o["oi"] for o in oi_data]

    if not values:
        return None

    first = values[0]
    current = values[-1]
    change_pct = ((current - first) / first * 100) \
        if first else 0

    if change_pct > 1:
        state = "RISING (trend strengthening)"
        state_color = GREEN
    elif change_pct < -1:
        state = "FALLING (trend weakening)"
        state_color = RED
    else:
        state = "flat"
        state_color = FG

    fig, ax = plt.subplots(figsize=(10, 3))

    ax.plot(xs, values, color=ORANGE, linewidth=2)
    ax.fill_between(
        xs, values, alpha=0.2, color=ORANGE,
    )

    title = symbol.replace("USDT", "")
    ax.set_title(
        title + " - Open Interest | "
        + format(change_pct, "+.2f") + "% | " + state,
        color=state_color, fontsize=11,
    )
    ax.set_ylabel("OI", color=FG, fontsize=9)
    ax.set_xlabel("Hours ago -> now", color=FG, fontsize=9)
    ax.grid(True, alpha=0.2)
    ax.tick_params(labelsize=8)

    plt.tight_layout()
    plt.savefig(output_path, dpi=90, facecolor=BG)
    plt.close(fig)

    log.info("oi saved: " + output_path)
    return output_path


def plot_anomaly_timeline(symbol, events, anomalies=None,
                          output_path=None):
    if not events and not anomalies:
        return None

    if output_path is None:
        output_path = "/tmp/" + symbol + "_timeline.png"

    fig, ax = plt.subplots(figsize=(10, 2.5))

    if events:
        xs = [e["timestamp"] for e in events]
        ys = [e.get("change_pct", 0) for e in events]
        colors = [GREEN if y > 0 else RED for y in ys]
        ax.scatter(xs, [1] * len(xs), c=colors,
                   s=80, alpha=0.7)

    if anomalies:
        xs = [a["timestamp"] for a in anomalies]
        ax.scatter(
            xs, [0] * len(xs), c=YELLOW, s=120,
            marker="v", alpha=0.9,
        )

    ax.axhline(y=0.5, color=GRID, linewidth=0.5)
    ax.set_ylim(-0.5, 1.5)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["Anomalies", "Events"],
                       color=FG, fontsize=9)

    title = symbol.replace("USDT", "")
    ax.set_title(title + " - last 7 days",
                 color=FG, fontsize=11)
    ax.grid(True, alpha=0.2, axis="x")
    ax.tick_params(labelsize=8)

    if events:
        ax.xaxis.set_major_formatter(
            mdates.DateFormatter("%d.%m")
        )

    plt.tight_layout()
    plt.savefig(output_path, dpi=90, facecolor=BG)
    plt.close(fig)

    log.info("timeline saved: " + output_path)
    return output_path