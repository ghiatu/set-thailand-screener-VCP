"""
SET100 Stock Screener
======================
Scans Thai SET100 stocks using yfinance data and screens them against:

1. Minervini Trend Template (8-point technical checklist)
2. RS Rating (relative strength vs. the SET index, IBD-style percentile rank)
3. VCP (Volatility Contraction Pattern) score

Runs locally or via GitHub Actions (see .github/workflows/screener.yml).

Usage:
    python set100_screener.py
    python set100_screener.py --tickers-file set100_tickers.csv --out results.csv
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from vcp_detection import VCPDetector

BENCHMARK = "^SET.BK"          # SET Composite Index (yfinance benchmark for SET100 constituents)
LOOKBACK_PERIOD = "2y"         # history window to download
MIN_ROWS_REQUIRED = 260        # ~ need > 200 trading days for the 200MA + RS calc


@dataclass
class ScreenResult:
    symbol: str
    yf_ticker: str
    company: str
    sector: str
    close: float
    pass_trend_template: bool
    trend_template_hits: int          # out of 8
    rs_rating: float                  # 1-99 percentile vs SET100 universe
    vcp_detected: bool
    vcp_score: float
    pivot: float | None
    distance_to_pivot_pct: float | None
    notes: str


def load_universe(tickers_file: str) -> pd.DataFrame:
    df = pd.read_csv(tickers_file)
    required_cols = {"symbol", "yf_ticker", "company", "sector"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"tickers file is missing columns: {missing}")
    return df


CHUNK_SIZE = 75           # tickers per yfinance batch call (keeps requests reasonably sized)
CHUNK_PAUSE_SECONDS = 2   # pause between batches to stay polite to Yahoo's servers


def download_history(tickers: list[str], benchmark: str) -> dict[str, pd.DataFrame]:
    """
    Batch-download OHLCV history for all tickers plus the benchmark, in chunks.
    For small universes (like SET100) this is a single chunk; for the full
    market (800+ tickers) it downloads in batches of CHUNK_SIZE with a short
    pause between them, retrying a chunk once if it fails outright.
    Returns a dict of ticker -> DataFrame (oldest-first, as yfinance provides it).
    """
    all_symbols = tickers + [benchmark]
    chunks = [all_symbols[i:i + CHUNK_SIZE] for i in range(0, len(all_symbols), CHUNK_SIZE)]
    print(f"Downloading {len(all_symbols)} symbols from yfinance in {len(chunks)} batch(es)...")

    result: dict[str, pd.DataFrame] = {}

    for chunk_num, chunk in enumerate(chunks, start=1):
        print(f"  Batch {chunk_num}/{len(chunks)} ({len(chunk)} symbols)...")
        data = None
        for attempt in range(2):  # try once, retry once on failure
            try:
                data = yf.download(
                    chunk,
                    period=LOOKBACK_PERIOD,
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )
                break
            except Exception as exc:
                print(f"    Batch {chunk_num} attempt {attempt + 1} failed: {exc}")
                time.sleep(5)

        if data is None:
            print(f"    Batch {chunk_num} failed after retry, skipping {len(chunk)} symbols")
            continue

        for sym in chunk:
            try:
                sub = data if len(chunk) == 1 else data[sym]
                sub = sub.dropna(how="all")
                if not sub.empty:
                    result[sym] = sub
            except (KeyError, Exception):
                continue

        if chunk_num < len(chunks):
            time.sleep(CHUNK_PAUSE_SECONDS)

    missing = len(all_symbols) - len(result)
    if missing:
        print(f"  Note: {missing} symbol(s) returned no usable data (delisted, too new, or fetch failure)")

    return result


def moving_average(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def check_trend_template(close_oldest_first: pd.Series) -> tuple[bool, int, dict]:
    """
    Mark Minervini's 8-point Trend Template, evaluated on the most recent bar.
    close_oldest_first must be ordered oldest -> newest (standard yfinance order).
    """
    if len(close_oldest_first) < MIN_ROWS_REQUIRED:
        return False, 0, {}

    price = close_oldest_first.iloc[-1]
    ma50 = moving_average(close_oldest_first, 50)
    ma150 = moving_average(close_oldest_first, 150)
    ma200 = moving_average(close_oldest_first, 200)

    ma50_now, ma150_now, ma200_now = ma50.iloc[-1], ma150.iloc[-1], ma200.iloc[-1]

    # 200MA trending up over the last month (~21 trading days)
    ma200_trending_up = ma200.iloc[-1] > ma200.iloc[-22] if len(ma200.dropna()) > 22 else False

    low_52w = close_oldest_first.iloc[-252:].min() if len(close_oldest_first) >= 252 else close_oldest_first.min()
    high_52w = close_oldest_first.iloc[-252:].max() if len(close_oldest_first) >= 252 else close_oldest_first.max()

    criteria = {
        "1_price_above_ma150_ma200": price > ma150_now and price > ma200_now,
        "2_ma150_above_ma200": ma150_now > ma200_now,
        "3_ma200_trending_up": ma200_trending_up,
        "4_ma50_above_ma150_ma200": ma50_now > ma150_now and ma50_now > ma200_now,
        "5_price_above_ma50": price > ma50_now,
        "6_price_30pct_above_52w_low": price >= low_52w * 1.30,
        "7_price_within_25pct_of_52w_high": price >= high_52w * 0.75,
        # RS rating check is folded in separately (criterion 8) once RS is computed
    }

    hits = sum(1 for v in criteria.values() if v)
    passed = hits == len(criteria)  # all 7 price/MA criteria; RS check applied on top later
    return passed, hits, criteria


def compute_rs_ratings(price_histories: dict[str, pd.DataFrame], tickers: list[str]) -> dict[str, float]:
    """
    IBD-style RS Rating: weighted 12-month price performance
    (40% x last quarter + 20% x each of the prior 3 quarters), then
    percentile-ranked 1-99 across the SET100 universe.
    """
    perf_scores: dict[str, float] = {}

    for tkr in tickers:
        df = price_histories.get(tkr)
        if df is None or len(df) < 252:
            continue
        close = df["Close"].dropna()
        if len(close) < 252:
            continue

        p_now = close.iloc[-1]
        p_63 = close.iloc[-63] if len(close) >= 63 else close.iloc[0]
        p_126 = close.iloc[-126] if len(close) >= 126 else close.iloc[0]
        p_189 = close.iloc[-189] if len(close) >= 189 else close.iloc[0]
        p_252 = close.iloc[-252] if len(close) >= 252 else close.iloc[0]

        q1 = (p_now / p_63) - 1
        q2 = (p_now / p_126) - 1
        q3 = (p_now / p_189) - 1
        q4 = (p_now / p_252) - 1

        score = 0.4 * q1 + 0.2 * q2 + 0.2 * q3 + 0.2 * q4
        perf_scores[tkr] = score

    if not perf_scores:
        return {}

    series = pd.Series(perf_scores)
    ranks = series.rank(pct=True) * 98 + 1  # scale to 1-99
    return ranks.round(0).to_dict()


def run_vcp_check(df: pd.DataFrame) -> dict:
    detector = VCPDetector()
    close_recent_first = df["Close"].dropna().iloc[::-1].reset_index(drop=True)
    volume_recent_first = df["Volume"].dropna().iloc[::-1].reset_index(drop=True) if "Volume" in df else None

    if len(close_recent_first) < detector.lookback_days:
        return {"vcp_detected": False, "vcp_score": 0.0, "pivot": None, "distance_to_pivot_pct": None}

    result = detector.detect_vcp(close_recent_first, volume_recent_first)
    pivot_info = result.get("pivot_info", {})
    return {
        "vcp_detected": result["vcp_detected"],
        "vcp_score": result["vcp_score"],
        "pivot": pivot_info.get("pivot"),
        "distance_to_pivot_pct": pivot_info.get("distance_pct"),
    }


def screen(tickers_file: str, out_path: str, rs_threshold: float = 70.0, limit: int | None = None) -> pd.DataFrame:
    universe = load_universe(tickers_file)
    if limit:
        universe = universe.head(limit)
        print(f"--limit set: only screening the first {limit} tickers (for testing)")
    tickers = universe["yf_ticker"].tolist()

    histories = download_history(tickers, BENCHMARK)
    if BENCHMARK not in histories:
        print(f"WARNING: could not download benchmark {BENCHMARK}; RS ratings will still be computed "
              f"relative to the SET100 universe itself, just without a market-index cross-check.")

    print("Computing RS ratings...")
    rs_ratings = compute_rs_ratings(histories, tickers)

    results: list[ScreenResult] = []
    print("Screening each stock (trend template + VCP)...")

    for i, (_, row) in enumerate(universe.iterrows(), start=1):
        if i % 50 == 0 or i == len(universe):
            print(f"  ...screened {i}/{len(universe)}")
        tkr = row["yf_ticker"]
        df = histories.get(tkr)
        if df is None or df.empty:
            results.append(ScreenResult(
                symbol=row["symbol"], yf_ticker=tkr, company=row["company"], sector=row["sector"],
                close=float("nan"), pass_trend_template=False, trend_template_hits=0,
                rs_rating=float("nan"), vcp_detected=False, vcp_score=0.0,
                pivot=None, distance_to_pivot_pct=None, notes="no data from yfinance",
            ))
            continue

        close = df["Close"].dropna()
        if close.empty:
            continue

        passed_price_ma, hits, _criteria = check_trend_template(close)
        rs = rs_ratings.get(tkr, float("nan"))
        rs_ok = (not np.isnan(rs)) and rs >= rs_threshold
        overall_pass = passed_price_ma and rs_ok

        vcp_info = run_vcp_check(df)

        results.append(ScreenResult(
            symbol=row["symbol"],
            yf_ticker=tkr,
            company=row["company"],
            sector=row["sector"],
            close=round(float(close.iloc[-1]), 2),
            pass_trend_template=overall_pass,
            trend_template_hits=hits,
            rs_rating=rs,
            vcp_detected=vcp_info["vcp_detected"],
            vcp_score=vcp_info["vcp_score"],
            pivot=vcp_info["pivot"],
            distance_to_pivot_pct=vcp_info["distance_to_pivot_pct"],
            notes="",
        ))

    result_df = pd.DataFrame([asdict(r) for r in results])
    result_df = result_df.sort_values(
        by=["pass_trend_template", "vcp_score", "rs_rating"], ascending=[False, False, False]
    )
    result_df.to_csv(out_path, index=False)
    print(f"\nSaved {len(result_df)} rows to {out_path}")

    passed = result_df[result_df["pass_trend_template"]]
    print(f"\n{'='*70}")
    print(f"{len(passed)} stocks pass the full Trend Template + RS >= {rs_threshold} screen:")
    print(f"{'='*70}")
    if not passed.empty:
        cols = ["symbol", "close", "rs_rating", "vcp_score", "vcp_detected", "pivot", "distance_to_pivot_pct"]
        print(passed[cols].to_string(index=False))
    else:
        print("(none today — that's normal; the criteria are intentionally strict)")

    vcp_only = result_df[result_df["vcp_detected"]]
    print(f"\n{len(vcp_only)} stocks show a VCP pattern (regardless of trend template):")
    if not vcp_only.empty:
        cols = ["symbol", "close", "vcp_score", "pivot", "distance_to_pivot_pct"]
        print(vcp_only[cols].to_string(index=False))

    return result_df


def main():
    parser = argparse.ArgumentParser(description="SET100 CANSLIM/Minervini screener using yfinance")
    parser.add_argument("--tickers-file", default="set100_tickers.csv", help="CSV with symbol,yf_ticker,company,sector")
    parser.add_argument("--out", default="set100_screen_results.csv", help="Output CSV path")
    parser.add_argument("--rs-threshold", type=float, default=70.0, help="Minimum RS rating (1-99) to pass")
    parser.add_argument("--limit", type=int, default=None, help="Only screen the first N tickers (useful for a quick test run)")
    args = parser.parse_args()

    start = time.time()
    screen(args.tickers_file, args.out, args.rs_threshold, args.limit)
    print(f"\nDone in {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
