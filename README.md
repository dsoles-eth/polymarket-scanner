# 🔍 Polymarket Scanner

> A production-ready prediction market opportunity scanner with Kelly Criterion position sizing, circuit breakers, and Discord alerts.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)

---

## What It Does

Polymarket Scanner continuously monitors active prediction markets on [Polymarket](https://polymarket.com), the world's largest decentralised prediction market. It identifies markets where the crowd-implied probability appears mispriced relative to a fair-value estimate, then sizes a recommended position using the Kelly Criterion.

**Paper trading is the default.** The scanner logs opportunities to a JSON file and posts them to Discord — it never moves real money unless you explicitly configure live mode.

---

## Features

| Feature | Description |
|---|---|
| 📊 **Market Scanning** | Fetches 100+ active markets every run via Gamma API |
| 💹 **Live Prices** | Pulls real-time YES/NO prices from the Polymarket CLOB |
| 🧮 **Kelly Sizing** | Mathematically optimal bet sizing with a 5% hard cap |
| 🚦 **Circuit Breakers** | Auto-pauses a category after 3 consecutive losses |
| 📝 **Paper Trading** | Safe-by-default: logs bets to JSON, never executes |
| 🔔 **Discord Alerts** | Posts ranked opportunities to your signals channel |
| ⚙️ **Configurable** | All thresholds tunable via `.env` |
| 🕐 **Scheduled** | macOS launchd plist included (runs every 15 min) |

---

## How Polymarket Works

Polymarket is a prediction market where you can bet on real-world events using USD-pegged stablecoins.

- Every market has two outcomes: **YES** and **NO**
- Each outcome is represented by a **token** that trades between **$0.00 and $1.00**
- A YES token price of **$0.65** means the market believes there is a **65% chance** the event happens
- When the event resolves, winning tokens pay out **$1.00**; losing tokens pay out **$0.00**
- The market uses a **Central Limit Order Book (CLOB)** — just like a stock exchange

This creates a market-derived probability for almost any event: elections, crypto prices, geopolitics, sports, and more.

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/yourusername/polymarket-scanner.git
cd polymarket-scanner

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — at minimum set DISCORD_CHANNEL_SIGNALS and DISCORD_BOT_TOKEN
```

### 3. Run

```bash
python scanner.py
```

Opportunities are logged to `data/paper_trades.json` and posted to Discord.

### 4. Schedule (macOS)

```bash
# Edit the paths in the plist to match your system first
cp launchd/com.dsoles.polymarket-scanner.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.dsoles.polymarket-scanner.plist
```

The scanner will run every **15 minutes** automatically.

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `DISCORD_CHANNEL_SIGNALS` | *(required)* | Discord channel ID for opportunity alerts |
| `DISCORD_BOT_TOKEN` | *(required)* | Discord bot token (keep secret!) |
| `MIN_LIQUIDITY` | `1000` | Minimum USD liquidity to consider a market |
| `MAX_DAYS_TO_RESOLUTION` | `90` | Skip markets resolving further than this |
| `MIN_EDGE_PCT` | `3` | Minimum edge (%) before flagging an opportunity |
| `PAPER_TRADING` | `true` | `true` = log only; `false` = live execution |
| `BANKROLL` | `1000` | Total capital for Kelly sizing (USD) |
| `MAX_KELLY_FRACTION` | `0.05` | Hard cap: max bet as fraction of bankroll |
| `MAX_CONSECUTIVE_LOSSES` | `3` | Losses before a category circuit breaker trips |

---

## Strategy Deep-Dive

### What Is "Edge"?

In prediction markets, **edge** is the gap between your probability estimate and the market's implied probability.

```
Edge = (your estimate − market implied probability) × 100
```

If you think an event has a **60% chance** of happening but the market is pricing YES tokens at **$0.52** (52%), you have **+8% edge**. The market is underpricing the outcome relative to your model.

Edge only matters if your probability estimate is *correct*. The scanner uses a simple mean-reversion prior by default — in production you'd replace this with a proper model (news feeds, base rates, domain expertise).

### Kelly Criterion

The Kelly Criterion answers: *"What fraction of my bankroll should I bet to maximise long-run growth?"*

```
kelly_fraction = (p × b − (1 − p)) / b
```

Where:
- `p` = your probability of winning
- `b` = net decimal odds = `(1 / yes_price) − 1`

**Example:** You think YES has 60% probability; market prices it at $0.40 (implies 40%).
- Net odds `b = (1/0.40) − 1 = 1.5`
- Kelly = `(0.60 × 1.5 − 0.40) / 1.5 = 0.333` → 33.3% of bankroll

That's full Kelly — highly aggressive. We apply **half-Kelly** (×0.5) and a **5% hard cap** to control variance:

```
recommended_bet = min(kelly_fraction × 0.5, 0.05) × bankroll
```

Half-Kelly roughly halves your bet size and dramatically reduces the risk of ruin while still capturing most of the long-run growth benefit.

### Circuit Breakers

Markets can be locally unpredictable even when your edge is real. The circuit breaker tracks consecutive losses per category (politics, crypto, sports, etc.). After 3 consecutive losses, that category is paused until you manually reset `data/circuit_breakers.json`.

---

## Project Structure

```
polymarket-scanner/
├── scanner.py            # Main entry point
├── market_analyzer.py    # Pure analysis functions (Kelly, edge, parsing)
├── clob_client.py        # CLOB API client with rate limiting
├── config.py             # .env loader
├── requirements.txt
├── .env.example          # Copy to .env
├── .gitignore
├── LICENSE
├── README.md
├── launchd/
│   └── com.dsoles.polymarket-scanner.plist  # macOS scheduler
└── data/
    ├── .gitkeep
    ├── paper_trades.json       # Generated — paper trade log
    └── circuit_breakers.json   # Generated — loss tracking
```

---

## ⚠️ Disclaimer

**Prediction markets involve real financial risk.**

- Past edge does not guarantee future returns
- Markets can move against you quickly and without warning
- Never bet more than you can afford to lose
- This software is provided for educational and research purposes
- The authors are not financial advisors; this is not financial advice
- Always start in paper trading mode and validate your edge before going live
- Comply with the terms of service of any platform you use and the laws of your jurisdiction

---

## License

MIT License — Copyright (c) 2026 Dsoles. See [LICENSE](LICENSE) for full terms.
