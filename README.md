# Gold & Stock Alert Bot

Sends scheduled updates to a Telegram channel with:
- Accenture (ACN) stock price and any other stocks you configure
- Gold spot price in USD/oz, MYR/oz, and MYR/1g
- Live dealer prices for PAMP/999 1g gold bars from Malaysian websites

Runs on GitHub Actions - no laptop or server needed.

---

## Project Structure

```
MarketPulse/
├── main.py                        # main script (do not edit for normal use)
├── config.json                    # your local settings - gitignored, never pushed
├── config.example.json            # template committed to GitHub (no real credentials)
├── requirements.txt               # Python dependencies
├── run.bat                        # run locally on Windows
├── .gitignore                     # keeps config.json and logs out of Git
├── .github/
│   └── workflows/
│       ├── gold_alert.yml         # runs every hour (hourly sites only)
│       └── gold_alert_daily.yml   # runs once a day at 8:00 AM MYT (daily sites)
└── logs/
    └── alerts.log                 # auto-created, local runs only
```

---

## One-Time Setup

### 1. Python dependencies (local only)

**Windows (PowerShell)**
```powershell
cd path\to\MarketPulse
pip install -r requirements.txt
playwright install chromium
```

**WSL / Linux / macOS**

Create the venv in your home directory (not inside the project folder - venv on the Windows mount `/mnt/c/` does not work correctly in WSL):
```bash
python3 -m venv ~/venv-marketpulse
source ~/venv-marketpulse/bin/activate
cd path/to/MarketPulse
pip install -r requirements.txt
playwright install chromium
```

Next time you open a new terminal, re-activate before running:
```bash
source ~/venv-marketpulse/bin/activate
```

### 2. Create a Telegram bot

1. Open Telegram, search `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the **bot token** (looks like `123456789:ABCdef...`)
4. Create a Telegram channel
5. Add your bot as **Admin** with "Post Messages" permission
6. Your channel ID is either `@channelname` or a numeric ID like `-1001234567890`

### 3. Fill in config.json (local use)

Edit `config.json` and replace:
- `YOUR_BOT_TOKEN_HERE` with your bot token
- `@your_channel_username` with your channel ID

### 4. Test locally

```powershell
cd path\to\MarketPulse
python main.py
```

---

## GitHub Actions Setup (recommended - runs without your laptop)

### 1. Push code to GitHub

```powershell
cd path\to\MarketPulse
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/market-pulse.git
git push -u origin main
```

After pushing, log out of the Git session:
```powershell
git credential-manager github logout
```

### 2. Add secrets in GitHub

Go to your repo on GitHub: **Settings > Secrets and variables > Actions > New repository secret**

| Secret name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | your bot token from BotFather |
| `TELEGRAM_CHANNEL_ID` | your channel username or numeric ID |

### 3. Test manually

Go to your repo on GitHub: **Actions tab**

You will see two workflows:
- **Gold & Stock Alert - Hourly** - test this to check hourly sites
- **Gold & Stock Alert - Daily** - test this to check daily sites

Click either one > **Run workflow** > **Run workflow** to trigger it manually.

Free tier gives 2,000 minutes/month. This project uses roughly 1,125 minutes/month (hourly + daily workflows combined).

---

## Gold Spot Price Source

The script uses Yahoo Finance ticker `GC=F` which is the **COMEX gold futures** (front-month contract), not the true spot price. This is why the USD/oz price may look slightly different from what TradingView shows - TradingView's `XAUUSD` is the OTC interbank spot price, which is considered the "real" spot price. The difference is usually a few dollars per oz.

To switch to spot price (closer to TradingView), change the ticker in `main.py`:

```python
# Current (COMEX futures - default)
gold_usd_oz = yf.Ticker("GC=F").fast_info.last_price

# Spot price (closer to TradingView XAUUSD)
gold_usd_oz = yf.Ticker("XAUUSD=X").fast_info.last_price
```

---

## Adding Stocks

Edit the `"stocks"` array in `config.json`. The `ticker` is the Yahoo Finance symbol.

```json
"stocks": [
  { "ticker": "ACN",     "label": "Accenture" },
  { "ticker": "AAPL",    "label": "Apple" },
  { "ticker": "MSFT",    "label": "Microsoft" },
  { "ticker": "1155.KL", "label": "Maybank" }
]
```

Malaysian stocks use the `.KL` suffix. US stocks use the ticker as-is.

---

## Adding Gold Price URLs

Edit the `"sites"` array in `config.json`. Add a new entry:

```json
{
  "name": "Dealer Name - Product",
  "url": "https://dealer.com.my/product/pamp-1g",
  "method": "requests",
  "frequency_hours": 24,
  "price_selectors": [
    ".woocommerce-Price-amount bdi",
    ".price",
    "[itemprop='price']"
  ]
}
```

### frequency_hours

Controls how often the site is scraped. Must match one of the two workflows.

| Value | Workflow | When it runs |
|---|---|---|
| `1` | Hourly | Every hour |
| `24` | Daily | Once a day at 8:00 AM MYT |

### Choosing the right method

| Method | When to use |
|---|---|
| `requests` | Most sites - price is in the raw HTML (WooCommerce, static pages) |
| `playwright` | Dynamic sites - price is loaded by JavaScript after page renders |

**How to check:** Open the site, right-click > **View Page Source**, press `Ctrl+F` and search for the price number.
- Found it - use `requests`
- Not found - use `playwright`

Start with `requests`. If the site shows `N/A - price not found` in your Telegram message, switch to `playwright`.

### Common selectors for Malaysian gold sites

Most Malaysian dealers run WooCommerce. These selectors work for most of them:

```json
"price_selectors": [
  ".woocommerce-Price-amount bdi",
  ".price .woocommerce-Price-amount",
  "[itemprop='price']",
  ".amount"
]
```

For non-WooCommerce sites, try:

```json
"price_selectors": [
  ".product-price",
  "[class*='price']",
  ".price"
]
```

The script always falls back to a full-page scan for any `RM` value in the 250-2500 range if no selector matches.

---

## Deploying Config Changes

After editing `config.json`, push to GitHub and the next scheduled run picks it up:

```powershell
cd path\to\MarketPulse
git add config.example.json
git commit -m "Add new stock/site"
git push
git credential-manager github logout
```

---

## Sample Telegram Message

```
Gold & Market Update
01 Jul 2026, 14:00 MYT

Stocks
  Accenture (ACN): $320.50  -1.20 (-0.37%)
  Apple (AAPL): $195.80  +0.50 (+0.26%)

Gold Spot
  USD/oz  : $2,650.00
  MYR/oz  : RM 11,785.00
  MYR/1g  : RM 379.00
  USD/MYR : 4.4472

Dealer Prices - 1g Gold Bar
  SilverBullion MY - PAMP 1g: RM 440.00
  Aston & Sons - PAMP Fortuna 1g: N/A - timeout
  WahChan - As Salam 1g: N/A - blocked (403/429)
  LITZ - PAMP 1g: N/A - site unreachable
  MyBullionTrade - PAMP 1g: N/A - http 404
  MSGold - Fortuna 1g: N/A - price not found
```

### N/A status meanings

| Status | Meaning |
|---|---|
| `timeout` | Site took too long to respond |
| `blocked (403/429)` | Site is blocking the bot |
| `site unreachable` | DNS/connection failed - URL might be dead |
| `http 404` | Page not found - URL has changed |
| `http 500` | Server error on their end |
| `price not found` | Page loaded fine but price could not be extracted |

---

## Troubleshooting

**A site shows N/A**
- `http 404` or `site unreachable` - update the URL in `config.json`
- `blocked` - the site is rejecting cloud IPs (GitHub Actions runs on Azure); not much can be done
- `price not found` - the page loaded but the selector didn't match; switch `method` to `playwright` or update `price_selectors`
- `timeout` - temporary issue, usually resolves on next run

**Telegram message not sending**
- Verify the bot is Admin in your channel
- Double-check the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHANNEL_ID` secrets in GitHub

**GitHub Actions not running**
- Check the Actions tab in your repo for error logs
- Make sure workflow files are at `.github/workflows/`
