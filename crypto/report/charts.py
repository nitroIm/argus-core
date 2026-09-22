# ============================================================
# ARGUS-Trader — CHARTS v2
# ------------------------------------------------------------
# v2: fix — в plot_markov текст всегда тёмный (виден).
#     Рисует графики для Telegram-отчёта:
#     - свечи с уровнями
#     - 0/1 паттерн
#     - Markov матрица
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


def plot_candles(symbol, candles, supports=None, resistances=None,
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

    for i, c in enumerate(candles):
        color = GREEN if c["close"] >= c["open"] else RED
        ax2.bar(i, c["volume"], color=color, alpha=0.6, width=0.7)

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

    data = binary_string[-50:] if len(binary_string) >= 50 else binary_string
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
    ax.set_xticklabels(["-> UP", "-> DOWN"], color=FG, fontsize=10)
    ax.set_yticklabels(["from DOWN", "from UP"], color=FG, fontsize=10)

    # ВСЕГДА тёмный текст — видно на светлом фоне
    for i in range(2):
        for j in range(2):
            value = matrix[i, j]
            ax.text(
                j, i,
                format(value, ".2f"),
                ha="center", va="center",
                color=DARK,
                fontsize=15,
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


def plot_anomaly_timeline(symbol, events, anomalies=None, output_path=None):
    if not events and not anomalies:
        return None

    if output_path is None:
        output_path = "/tmp/" + symbol + "_timeline.png"

    fig, ax = plt.subplots(figsize=(10, 2.5))

    if events:
        xs = [e["timestamp"] for e in events]
        ys = [e.get("change_pct", 0) for e in events]
        colors = [GREEN if y > 0 else RED for y in ys]
        ax.scatter(xs, [1] * len(xs), c=colors, s=80, alpha=0.7)

    if anomalies:
        xs = [a["timestamp"] for a in anomalies]
        ax.scatter(
            xs, [0] * len(xs), c=YELLOW, s=120,
            marker="v", alpha=0.9,
        )

    ax.axhline(y=0.5, color=GRID, linewidth=0.5)
    ax.set_ylim(-0.5, 1.5)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["Anomalies", "Events"], color=FG, fontsize=9)

    title = symbol.replace("USDT", "")
    ax.set_title(title + " - last 7 days", color=FG, fontsize=11)
    ax.grid(True, alpha=0.2, axis="x")
    ax.tick_params(labelsize=8)

    if events:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))

    plt.tight_layout()
    plt.savefig(output_path, dpi=90, facecolor=BG)
    plt.close(fig)

    log.info("timeline saved: " + output_path)
    return output_path