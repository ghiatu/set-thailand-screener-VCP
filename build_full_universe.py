"""
build_full_universe.py
=======================
Converts SET's official "List of Listed Companies" Excel file into the
symbol,yf_ticker,company,sector CSV format used by set100_screener.py.

Download the source file yourself first (SET blocks automated downloads):
    https://www.set.or.th/dat/eod/listedcompany/static/listedCompanies_en_US.xls

Then run:
    python build_full_universe.py --input listedCompanies_en_US.xls --out thailand_all_tickers.csv

The script auto-detects which row holds the real header (SET's export usually
has a title/logo row or two before the actual table) by scanning for a cell
that looks like "Symbol". If auto-detection fails, it prints out what it
found so you can tell me and I'll adjust it.
"""
from __future__ import annotations

import argparse
import re
import sys

import pandas as pd

# Patterns for instrument types that are NOT plain common stock and won't
# have their own separate yfinance ticker (warrants, rights, NVDR/foreign
# board variants, etc). These get filtered out of the screening universe.
NON_COMMON_STOCK_SUFFIXES = re.compile(
    r"(?:-R$|-F$|-U$|-P$|W\d*$|-W\d*$)", re.IGNORECASE
)


def find_header_row(raw: pd.DataFrame, max_scan_rows: int = 10) -> int:
    """Scan the first few rows to find which one contains a 'Symbol' cell."""
    for i in range(min(max_scan_rows, len(raw))):
        row_values = raw.iloc[i].astype(str).str.strip().str.lower().tolist()
        if any(v == "symbol" for v in row_values):
            return i

    print("\nCould not find a row with a 'Symbol' cell. Here are the first "
          f"{min(max_scan_rows, len(raw))} rows so you can see what's actually there "
          "(copy this output back so the header detection can be adjusted):\n")
    print(raw.head(max_scan_rows).to_string())
    raise ValueError("Could not auto-detect the header row — see the printed rows above.")


def guess_column(columns: list[str], candidates: list[str]) -> str | None:
    lower_map = {c.lower().strip(): c for c in columns}
    for cand in candidates:
        if cand in lower_map:
            return lower_map[cand]
    # fallback: substring match
    for col in columns:
        for cand in candidates:
            if cand in col.lower():
                return col
    return None


def read_source_file(input_path: str) -> tuple[pd.DataFrame, bool]:
    """
    SET's "Excel" export is sometimes literally an HTML table saved with an
    .xls extension (a common old-school trick), which makes pandas.read_excel
    fail with "Excel file format cannot be determined". Try real Excel first,
    then fall back to reading it as HTML.

    Returns (dataframe, header_already_parsed):
      - Genuine Excel -> (raw grid with header=None, False) so the caller
        scans for the header row itself (SET's exports have title rows first).
      - HTML table -> (table with pandas' own inferred header, True), since
        pandas.read_html correctly parses <th>/first-row headers on its own.
    """
    # Try genuine Excel formats first
    for engine in ("xlrd", "openpyxl", "calamine"):
        try:
            return pd.read_excel(input_path, header=None, dtype=str, engine=engine), False
        except Exception:
            continue

    # Fall back: it's probably an HTML table wearing an .xls costume
    print("File isn't a real Excel binary — trying to read it as an HTML table instead...")
    try:
        tables = pd.read_html(input_path)
    except Exception as exc:
        raise ValueError(
            f"Could not read {input_path} as Excel or as HTML. Open it in a text "
            f"editor and check what it actually looks like. Original error: {exc}"
        )

    if not tables:
        raise ValueError(f"No tables found when parsing {input_path} as HTML")

    # Pick the largest table — the real data table, not some tiny nav/header table
    biggest = max(tables, key=lambda t: t.shape[0] * t.shape[1])
    print(f"Found {len(tables)} HTML table(s); using the largest one "
          f"({biggest.shape[0]} rows x {biggest.shape[1]} cols)")

    biggest = biggest.astype(str)
    # If read_html couldn't find real headers (no <th> tags), it falls back to
    # plain 0,1,2,... column labels. Detect that and hand it to the same
    # header-row scanner used for genuine Excel files.
    looks_unheadered = list(biggest.columns) == list(range(biggest.shape[1]))
    if looks_unheadered:
        print("HTML table has no real header row (no <th> tags) — scanning for it manually...")
        return biggest.reset_index(drop=True), False

    return biggest, True


def build_universe(input_path: str, out_path: str, market_filter: str | None = None) -> pd.DataFrame:
    print(f"Reading {input_path} ...")
    raw, header_already_parsed = read_source_file(input_path)

    if header_already_parsed:
        # pandas.read_html already turned the <th> row into real column names
        df = raw.copy()
        df.columns = [str(c).strip() for c in df.columns]
    else:
        header_row = find_header_row(raw)
        print(f"Detected header at row {header_row}")
        df = raw.iloc[header_row + 1:].copy()
        df.columns = raw.iloc[header_row].astype(str).str.strip().tolist()
        df = df.reset_index(drop=True)
    print(f"Columns found: {list(df.columns)}")
    print(f"Total rows: {len(df)}")

    symbol_col = guess_column(df.columns, ["symbol"])
    company_col = guess_column(
        df.columns, ["company name (english)", "company name", "name", "company"]
    )
    sector_col = guess_column(df.columns, ["sector", "industry"])
    market_col = guess_column(df.columns, ["market"])

    if symbol_col is None:
        print("ERROR: could not find a Symbol column. Columns available:", list(df.columns))
        sys.exit(1)

    print(f"Using: symbol={symbol_col!r}, company={company_col!r}, "
          f"sector={sector_col!r}, market={market_col!r}")

    out = pd.DataFrame()
    out["symbol"] = df[symbol_col].astype(str).str.strip()
    out["company"] = df[company_col].astype(str).str.strip() if company_col else ""
    out["sector"] = df[sector_col].astype(str).str.strip() if sector_col else "Unknown"
    if market_col:
        out["market"] = df[market_col].astype(str).str.strip()

    # Drop blank / NaN symbols
    out = out[out["symbol"].notna() & (out["symbol"].str.lower() != "nan") & (out["symbol"] != "")]

    # Optionally filter to just SET or just mai
    if market_filter and "market" in out.columns:
        before = len(out)
        out = out[out["market"].str.upper() == market_filter.upper()]
        print(f"Filtered to market={market_filter}: {before} -> {len(out)} rows")

    # Filter out warrants/rights/NVDR-style suffixes that don't have their own yfinance ticker
    before = len(out)
    out = out[~out["symbol"].str.contains(NON_COMMON_STOCK_SUFFIXES)]
    print(f"Filtered out warrant/rights/NVDR-style symbols: {before} -> {len(out)} rows")

    out["yf_ticker"] = out["symbol"] + ".BK"
    out = out.drop_duplicates(subset="symbol").sort_values("symbol")

    final_cols = ["symbol", "yf_ticker", "company", "sector"]
    out = out[final_cols]
    out.to_csv(out_path, index=False)
    print(f"\nWrote {len(out)} tickers to {out_path}")
    print(out.head(10).to_string(index=False))
    return out


def main():
    parser = argparse.ArgumentParser(description="Build a full Thai stock universe CSV from SET's listed-company export")
    parser.add_argument("--input", default="listedCompanies_en_US.xls", help="Path to the downloaded SET Excel file")
    parser.add_argument("--out", default="thailand_all_tickers.csv", help="Output CSV path")
    parser.add_argument("--market", default=None, choices=[None, "SET", "mai"],
                         help="Optional: keep only 'SET' or only 'mai' listed stocks (default: keep both)")
    args = parser.parse_args()
    build_universe(args.input, args.out, args.market)


if __name__ == "__main__":
    main()
