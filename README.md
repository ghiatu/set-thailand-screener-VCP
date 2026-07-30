# SET100 Screener

Scans Thai SET100 stocks (via `yfinance`) against:

1. **Mark Minervini's Trend Template** — 7 price/moving-average criteria + RS rating cutoff
2. **RS Rating** — IBD-style relative strength, percentile-ranked (1-99) across the SET100 universe
3. **VCP (Volatility Contraction Pattern)** — detects Minervini-style tightening consolidation bases, ported from [xang1234/stock-screener](https://github.com/xang1234/stock-screener) (Apache-2.0 license)

No Docker, no database, no server — just a Python script. Runs locally or on a schedule via GitHub Actions.

## Files

- `set100_screener.py` — main script
- `vcp_detection.py` — standalone VCP pattern detector
- `set100_tickers.csv` — SET100 constituent list with `.BK` tickers (SET's H1/2025 official revision — **update this every 6 months**, see below)
- `.github/workflows/screener.yml` — runs the screener every weekday after market close and commits the results CSV back to the repo

## Run locally (SET100, 100 stocks)

```bash
pip install -r requirements.txt
python set100_screener.py
```

Results are saved to `set100_screen_results.csv` and a summary prints to the terminal.

Options:
```bash
python set100_screener.py --tickers-file set100_tickers.csv --out results.csv --rs-threshold 70
```

## Scan the WHOLE Thai market instead (SET + mai, 800+ stocks)

SET blocks automated downloads of their company list, so this is a 2-step manual + script process:

**Step 1 — Download the official list yourself (once):**
Open this link in your browser and save the file:
https://www.set.or.th/dat/eod/listedcompany/static/listedCompanies_en_US.xls

Save it into this same folder as `listedCompanies_en_US.xls`.

**Step 2 — Convert it into the screener's ticker format:**
```bash
pip install openpyxl xlrd
python build_full_universe.py --input listedCompanies_en_US.xls --out thailand_all_tickers.csv
```
This prints out how many tickers it found and detected columns — check that the numbers look
sane (SET + mai is normally 800-900+ common stocks) before moving on. It automatically drops
warrants, rights, NVDR, and foreign-board line items (symbols ending `-R`, `-F`, `-U`, `-P`,
`W1`, `W2`, etc.) since those don't have their own separate ticker on Yahoo Finance.

Optional: keep only one market —
```bash
python build_full_universe.py --input listedCompanies_en_US.xls --out set_only.csv --market SET
python build_full_universe.py --input listedCompanies_en_US.xls --out mai_only.csv --market mai
```

**Step 3 — Run the screener against the full universe:**
```bash
python set100_screener.py --tickers-file thailand_all_tickers.csv --out thailand_screen_results.csv
```

This will take noticeably longer than the SET100 run (downloads happen in batches of 75 tickers
with a short pause between batches to avoid Yahoo rate-limiting) — expect several minutes rather
than seconds. Do a quick smoke test first with a small subset:
```bash
python set100_screener.py --tickers-file thailand_all_tickers.csv --limit 20 --out test_results.csv
```

If `build_full_universe.py` can't find the header row or the right columns automatically (SET
occasionally tweaks their export format), it will print out what it found — share that output
and the column layout can be adjusted.

## Running the full-market scan via GitHub Actions

GitHub Actions can't download `listedCompanies_en_US.xls` itself (SET blocks automated fetches
the same way it blocked mine) — so generate `thailand_all_tickers.csv` locally using Steps 1-2
above, then commit that CSV to the repo. Update `.github/workflows/screener.yml`'s
`Run screener` step to point at it:
```yaml
      - name: Run screener
        run: python set100_screener.py --tickers-file thailand_all_tickers.csv --out thailand_screen_results.csv
```
Re-generate and re-commit `thailand_all_tickers.csv` every few months as SET adds/removes listings.

## Run via GitHub Actions

1. Push this folder to a GitHub repo.
2. In the repo Settings → Actions → General, make sure "Read and write permissions" is enabled for the `GITHUB_TOKEN` (needed so the workflow can commit the results file back).
3. The workflow runs automatically every weekday at 17:00 Bangkok time, or trigger it manually from the **Actions** tab → **SET100 Screener** → **Run workflow**.

## Keeping the SET100 list current

SET revises SET50/SET100 constituents twice a year (around January and July). Check the official list at:
https://www.set.or.th/en/market/information/securities-list/constituents-list-set50-set100

Update `set100_tickers.csv` when the list changes — it's a plain CSV, easy to edit by hand or regenerate.

## Notes

- `^SET.BK` is used as the benchmark index for context; RS ratings themselves are ranked against the SET100 universe.
- The Trend Template requires ~260 trading days of history, so very recently listed stocks will show `pass_trend_template = False` with `notes` explaining why.
- VCP scoring parameters were tuned on US market data originally — thresholds (especially the volume contraction ratio) may need adjusting for Thai stocks with thinner trading volume. Worth backtesting against SET50 history before trusting the `vcp_detected` flag blindly.
