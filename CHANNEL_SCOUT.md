# Channel Scout — Daily Findings

Automatically updated daily at 7:00 PM IST by `scripts/channel_scout.py`.
New entries are appended each day. Review before subscribing to any channel.

> **How to use:** Subscribe to a channel on Telegram, let the bridge pick up messages
> for a few weeks, then check the weekly scorecard to see if it's worth keeping.
> Re-enable with `python main.py discover` after subscribing.

---

## Curated Seed List — 2026-04-05

Manually researched from: monetizedeal.com, extrape.com, earnkaro.com, moneymint.com,
TradingQnA, marketplustrading.com, and direct Telegram channel inspection.
Cross-referenced by mention frequency — higher count = more community trust signal.

### Priority 1 — Highest mention count, SEBI-registered, data-focused

These are the ones worth subscribing to first. SEBI registration means the analyst
is accountable; data/OI channels have no accuracy claims to fake.

| Handle | t.me link | Focus | Subscribers | SEBI | Mentions |
|---|---|---|---|---|---|
| @fiidata | t.me/fiidata | FnO participant OI data, FII/DII flows, trading psychology | 11K | No (educational) | 2 |
| @Stockizenofficial | t.me/Stockizenofficial | Equity + options + institutional flows + macro (CA Vivek Khatri) | 51K | INH000017675 | 2 |
| @RakeshAlgo | t.me/RakeshAlgo | TA, Nifty, midcap, commodities, algo signals (Dr. Rakesh Bansal) | 35K | INH100008984 | 3 |
| @indextradingnitin | t.me/indextradingnitin | Nifty index options, intraday, PCR analysis (CA Nitin Murarka, SMC) | 193K | SMC-affiliated | 2 |
| @abhayvarn | t.me/abhayvarn | Nifty, Sensex, BankNifty options, equity intraday (CA Abhay Kumar) | 45K | INH300008465 | 2 |
| @MarketPlusTrading | t.me/MarketPlusTrading | Nifty, BankNifty, FinNifty, FII/DII data, OI analysis | Unconfirmed | SEBI RA (team) | 2 |
| @mystockmarketfunda | t.me/mystockmarketfunda | TA, price action, education, Nifty/BankNifty, commodities | 12K | NSE Authorized Person | 2 |
| @PivotFunda | t.me/PivotFunda | Nifty OI, PCR every 5 min, option chain, support/resistance | 1K | No (data channel) | 2 |

### Priority 2 — High mention count, SEBI-registered, signal channels

Good to monitor once Priority 1 channels have been assessed. Signals channels
need 4+ weeks of live grading before trusting accuracy claims.

| Handle | t.me link | Focus | Subscribers | SEBI | Mentions |
|---|---|---|---|---|---|
| @STOCKGAINERSS | t.me/STOCKGAINERSS | Equity intraday, futures, options, BTST, swing, Nifty (Kapil Verma) | 120K | INH100007879 | 5+ |
| @equity99 | t.me/equity99 | Intraday, equity, mutual funds | 143K | Yes | 4+ |
| @chaseAlpha | t.me/chaseAlpha | Options, Nifty, BankNifty, equity, events | 27K | SEBI IA | 4+ |
| @joinstocktime | t.me/joinstocktime | Options, intraday, equity, education | 90K | Yes | 4+ |
| @TradeWithKarol_Prateek | t.me/TradeWithKarol_Prateek | Equity, Nifty, BankNifty options, daily setups (Prateek Karol) | 90K | Yes | 3+ |
| @meharshbhagat01 | t.me/meharshbhagat01 | Intraday, swing, free positional calls (Harsh Bhagat) | 197K | Yes | 3+ |
| @Flyingcalls_arjun | t.me/Flyingcalls_arjun | Stocks, indices, risk management, swing (Arjun Loganathan) | 67K | SEBI RA | 3+ |

### Priority 3 — Lower corroboration or non-SEBI, monitor cautiously

Worth watching but treat with more skepticism. Let the EOD grader score them.

| Handle | t.me link | Focus | Subscribers | SEBI | Mentions |
|---|---|---|---|---|---|
| @TradelikeFiis | t.me/TradelikeFiis | Educational trades, admin's personal analysis | 1.4K | No | 2 |
| @Banknifty_specials | t.me/Banknifty_specials | Nifty + BankNifty options, Sensex, intraday equity | 14K | No | 3 |
| @stock_burner_03 | t.me/stock_burner_03 | Nifty, BankNifty, TA, free calls, educational | 108K | No | 2 |
| @Ghanshyamtechanalysis0 | t.me/Ghanshyamtechanalysis0 | Options strategies, educational TA | 69K | No | 2 |
| @deltatrading1 | t.me/deltatrading1 | Options, BankNifty, F&O, intraday | 37K | Claimed | 2 |

### Do not add — Red flags noted

These were found in research but carry specific warnings:

| Handle | Reason to avoid |
|---|---|
| @options_trading_free | Claims 99% accuracy; mentions "account handling" (illegal under SEBI) |
| @options_guidT1 | Offers account handling services (illegal) |
| @Zero_To_Hero_trading_tipss | Account handling + unverified SEBI claims |
| @teamcepe4 | Account handling mentioned |
| @VijayWealthadvisorOfficial2020 | Explicitly non-SEBI per sources, high risk |
| @UshaAnalysis_SebiRegistered | Subscriber count wildly inconsistent across sources (413K vs 904K) |

### Verify SEBI registration

Before subscribing to any SEBI-registered channel, verify the registration number at:
**sebi.gov.in → Intermediaries → Research Analyst**

Registration numbers to verify:
- INH100007879 (@STOCKGAINERSS / Kapil Verma)
- INH000017675 (@Stockizenofficial / CA Vivek Khatri)
- INH100008984 (@RakeshAlgo / Dr. Rakesh Bansal)
- INH300008465 (@abhayvarn / CA Abhay Kumar)
- INH000021720 (Bharath's Market Research / Bharath Billa)

---

## How automated daily scouting works

`scripts/channel_scout.py` runs daily at 7:00 PM IST. It:
1. Scrapes Reddit (IndianStreetBets, IndiaInvestments, IndianStockMarket) and TradingQnA
2. Extracts Telegram handles mentioned near trading-related keywords
3. Filters: ≥2 independent mentions, not already in this file or monitored_channels DB
4. Appends new findings as a dated section below
5. Sends a Telegram alert summarising what was found

New automated scout entries appear below, dated and unreviewed.
Manually curated entries above are the starting point.

---
