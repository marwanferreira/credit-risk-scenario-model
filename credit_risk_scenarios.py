#!/usr/bin/env python3
"""Credit Risk Management: Theory and Application - first-order scenario repricing of three synthetic bond portfolios.

Conditional scenario analysis only: no regression, default forecasting or macro-to-spread elasticity.
P&L is instantaneous, in local currency, as a percentage of initial portfolio market value.

Run (downloads the three 2022 benchmark series):
    python3 credit_risk_scenarios.py
Run from local exports instead of the network (any subset):
    python3 credit_risk_scenarios.py --usd-csv raw/usd.csv --cad-csv raw/cad.csv --eur-csv raw/eur.csv
Skip the network entirely (designed stresses only unless local CSVs are given):
    python3 credit_risk_scenarios.py --offline
Exit code: 0 all checks pass; 2 designed stresses pass but the historical case was omitted; 1 a model check failed.
"""
import argparse
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests

# ---------------------------------------------------------------- inputs: sources (observed data)
START, END = "2022-01-01", "2022-12-31"
SOURCES = {
    "USD": dict(provider="Federal Reserve H.15 via FRED", series_id="DGS10",
                definition="10-year constant maturity Treasury yield", units="percent",
                url=f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd={START}&coed={END}"),
    "CAD": dict(provider="Bank of Canada Valet", series_id="BD.CDN.10YR.DQ.YLD",
                definition="Benchmark bond yield, 10-year: Government of Canada 10-year benchmark bond", units="percent",
                note=("Replaces V39055 named in the project brief, which Valet returns as 'Series V39055 not found' "
                      "(HTTP 404). Override with --cad-series."),
                url="https://www.bankofcanada.ca/valet/observations/{sid}/csv?start_date=" + START + "&end_date=" + END),
    "EUR": dict(provider="ECB Data Portal", series_id="YC.B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y",
                definition="Daily 10-year AAA euro-area government spot rate (public benchmark proxy)",
                units="percent",
                url=("https://data-api.ecb.europa.eu/service/data/YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_10Y"
                     f"?startPeriod={START}&endPeriod={END}&format=csvdata")),
}
MISSING_MARKERS = {"", ".", "NA", "N/A", "NaN", "nan", "ND"}

# ---------------------------------------------------------------- inputs: synthetic holdings (illustrative)
SLEEVES = pd.DataFrame([
    # portfolio, sleeve key, label, weight, benchmark duration, spread duration
    ("USD", "ust",  "US Treasury",                             0.70, 6.0, 0.0),
    ("USD", "muni", "Taxable US state/local proxy",            0.30, 6.0, 6.0),
    ("CAD", "goc",  "Government of Canada",                    0.70, 6.0, 0.0),
    ("CAD", "prov", "Canadian provincial proxy",               0.30, 6.0, 6.0),
    ("EUR", "sp",   "French bank senior preferred proxy",      0.70, 6.0, 6.0),
    ("EUR", "snp",  "French bank senior non-preferred proxy",  0.30, 6.0, 6.0),
], columns=["portfolio", "sleeve", "sleeve_label", "weight", "d_rate", "d_spread"])
SLEEVES["input_type"] = "illustrative (synthetic normalization assumption)"

# ---------------------------------------------------------------- inputs: scenarios (bp)
HIST = "January month-end to December month-end 2022 benchmark repricing"
COMMON = "Common spread widening"
RISKOFF = "Risk-off with a bond rally"
SCENARIOS = {
    # rate shock per currency (None = observed), spread shock per sleeve (government sleeves always 0)
    HIST:    dict(rate=None, spread=dict(ust=0, muni=0, goc=0, prov=0, sp=0, snp=0),
                  input_type="observed rate moves; spread moves set to 0 by design"),
    COMMON:  dict(rate=dict(USD=0, CAD=0, EUR=0), spread=dict(ust=0, muni=50, goc=0, prov=50, sp=50, snp=50),
                  input_type="illustrative designed stress (no historical calibration)"),
    RISKOFF: dict(rate=dict(USD=-50, CAD=-50, EUR=-50), spread=dict(ust=0, muni=50, goc=0, prov=50, sp=75, snp=125),
                  input_type="illustrative designed stress (differentiated spreads are hypotheses)"),
}
SPREAD_SLEEVE = {"USD": "muni", "CAD": "prov", "EUR": "snp"}   # sleeve whose weight is varied
BENCH_SLEEVE = {"USD": "ust", "CAD": "goc", "EUR": "sp"}       # complement sleeve
MIX_WEIGHTS = [0.10, 0.30, 0.60]
SPREAD_DURATIONS = [4.0, 6.0, 8.0]
TOL = 1e-12


# ================================================================= data
def fetch(url, timeout=60):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "dauphine-credit-risk-script/1.0"})
    r.raise_for_status()
    return r.content


def parse(ccy, text, series_id):
    """Return DataFrame[date, value_text] located by named headers; tolerant of the Valet preamble."""
    text = text.lstrip("﻿")
    if ccy == "EUR":
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
        if "KEY" in df.columns:
            keys = set(df["KEY"]) - {""}
            if keys != {series_id}:
                raise ValueError(f"unexpected ECB series keys {keys}")
        return df.rename(columns={"TIME_PERIOD": "date", "OBS_VALUE": "value_text"})[["date", "value_text"]]
    lines = text.splitlines()
    want = series_id.strip('"')
    for i, line in enumerate(lines):
        cols = [c.strip().strip('"') for c in line.split(",")]
        if len(cols) == 2 and cols[1] == want and cols[0].lower() in {"date", "observation_date"}:
            df = pd.read_csv(io.StringIO("\n".join(lines[i:])), dtype=str, keep_default_na=False)
            df.columns = ["date", "value_text"]
            return df
    raise ValueError(f"no header row with columns (date, {want}) found")


def clean(ccy, raw):
    """Parse dates and numbers; drop missing markers; flag nonnumeric tokens and conflicting duplicates."""
    df = raw.copy()
    df["value_text"] = df["value_text"].astype(str).str.strip()
    df["date"] = pd.to_datetime(df["date"].str.strip(), format="%Y-%m-%d", errors="coerce")
    bad_dates = int(df["date"].isna().sum())
    df = df[df["date"].notna()]
    is_missing = df["value_text"].isin(MISSING_MARKERS)
    df["yield_pct"] = pd.to_numeric(df["value_text"].where(~is_missing), errors="coerce")
    nonnumeric = df[~is_missing & df["yield_pct"].isna()]
    df = df[df["yield_pct"].notna()]
    df = df[(df["date"] >= START) & (df["date"] <= END)]
    dup = df[df.duplicated("date", keep=False)]
    conflicts = dup.groupby("date")["yield_pct"].nunique()
    conflicts = conflicts[conflicts > 1]
    df = df[~df["date"].isin(conflicts.index)].drop_duplicates("date").sort_values("date")
    qa = dict(rows_raw=len(raw), unparseable_dates=bad_dates, missing_markers_dropped=int(is_missing.sum()),
              nonnumeric_tokens=sorted(set(nonnumeric["value_text"])), conflicting_duplicate_dates=
              [d.strftime("%Y-%m-%d") for d in conflicts.index], valid_obs=len(df),
              first_date=df["date"].min().strftime("%Y-%m-%d") if len(df) else None,
              last_date=df["date"].max().strftime("%Y-%m-%d") if len(df) else None)
    df.insert(0, "currency", ccy)
    return df[["currency", "date", "value_text", "yield_pct"]], qa


def load_benchmarks(args, outdir):
    raw_dir = outdir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest, series = [], {}
    local = {"USD": args.usd_csv, "CAD": args.cad_csv, "EUR": args.eur_csv}
    for ccy, src in SOURCES.items():
        sid = args.cad_series if ccy == "CAD" else src["series_id"]
        url = src["url"].format(sid=sid)
        row = dict(currency=ccy, provider=src["provider"], series_id=sid, definition=src["definition"],
                   units=src["units"], frequency="business daily", requested_period=f"{START} to {END}",
                   url=url, input_type="observed",
                   note=src.get("note", "") if sid == src["series_id"] else f"user override of {src['series_id']}")
        try:
            if local[ccy]:
                content = Path(local[ccy]).read_bytes()
                row.update(origin=f"local file {local[ccy]}", retrieved_utc=datetime.fromtimestamp(
                    Path(local[ccy]).stat().st_mtime, timezone.utc).isoformat(timespec="seconds") + " (file mtime)")
            elif args.offline:
                raise RuntimeError("offline mode and no local CSV supplied")
            else:
                content = fetch(url)
                row.update(origin="download", retrieved_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            raw_path = raw_dir / f"{ccy.lower()}_{sid.replace('/', '_')}.csv"
            raw_path.write_bytes(content)
            row.update(raw_file=str(raw_path.relative_to(outdir)), sha256=hashlib.sha256(content).hexdigest())
            df, qa = clean(ccy, parse(ccy, content.decode("utf-8"), sid))
            if qa["valid_obs"] == 0:
                raise ValueError("no valid 2022 observations")
            if qa["nonnumeric_tokens"] or qa["conflicting_duplicate_dates"]:
                raise ValueError(f"validation failed: {qa}")
            row.update(status="ok", **{k: json.dumps(v) if isinstance(v, list) else v for k, v in qa.items()})
            series[ccy] = df
        except Exception as exc:  # report the blocker, keep going
            msg = str(exc)
            if isinstance(exc, requests.HTTPError) and exc.response is not None:
                body = " ".join(exc.response.text.replace("﻿", "").split())
                msg = f"HTTP {exc.response.status_code}: {body[:200]}"
            row.update(status="FAILED", error=msg,
                       retrieved_utc=row.get("retrieved_utc", datetime.now(timezone.utc).isoformat(timespec="seconds")))
        manifest.append(row)
    return series, pd.DataFrame(manifest)


def historical_moves(series):
    """Intersect valid dates, take last common date per month, Jan->Dec change in bp. Returns (moves, monthly)."""
    if set(series) != set(SOURCES):
        return None, None, f"missing series: {sorted(set(SOURCES) - set(series))}"
    wide = pd.concat({c: s.set_index("date")["yield_pct"] for c, s in series.items()}, axis=1, join="inner")
    wide = wide.sort_index()
    monthly = wide.groupby(wide.index.to_period("M")).tail(1)
    monthly.index.name = "date"
    if len(monthly) != 12 or list(monthly.index.month) != list(range(1, 13)):
        return None, monthly, f"expected 12 monthly common observations, got {len(monthly)}"
    jan, dec = monthly.iloc[0], monthly.iloc[-1]
    moves = pd.DataFrame({
        "currency": list(SOURCES),
        "jan_common_date": monthly.index[0].strftime("%Y-%m-%d"),
        "jan_yield_pct": [jan[c] for c in SOURCES],
        "dec_common_date": monthly.index[-1].strftime("%Y-%m-%d"),
        "dec_yield_pct": [dec[c] for c in SOURCES],
    })
    moves["change_pp"] = moves["dec_yield_pct"] - moves["jan_yield_pct"]
    moves["change_bp"] = moves["change_pp"] * 100.0
    moves["input_type"] = "observed"
    return moves, monthly, None


# ================================================================= model
def reprice(sleeves, rate_bp, spread_bp):
    """First-order P&L per sleeve; rate_bp keyed by portfolio, spread_bp keyed by sleeve. Decimal + percent."""
    s = sleeves.copy()
    s["rate_shock_bp"] = s["portfolio"].map(rate_bp).astype(float)
    s["spread_shock_bp"] = s["sleeve"].map(spread_bp).astype(float)
    s["rate_contrib"] = -s["weight"] * s["d_rate"] * s["rate_shock_bp"] / 10_000
    s["spread_contrib"] = -s["weight"] * s["d_spread"] * s["spread_shock_bp"] / 10_000
    s["total_contrib"] = s["rate_contrib"] + s["spread_contrib"]
    for c in ["rate", "spread", "total"]:
        s[f"{c}_contrib"] += 0.0                     # normalise -0.0 to 0.0
        s[f"{c}_contrib_pct"] = s[f"{c}_contrib"] * 100
    return s


def summarize(s):
    g = s.groupby("portfolio", sort=False)[["rate_contrib", "spread_contrib", "total_contrib"]].sum()
    g["rate_pct"], g["spread_pct"], g["net_pct"] = g["rate_contrib"] * 100, g["spread_contrib"] * 100, g["total_contrib"] * 100
    return g.reset_index()


def with_mix(ccy, w):
    s = SLEEVES.copy()
    s.loc[s["sleeve"] == SPREAD_SLEEVE[ccy], "weight"] = w
    s.loc[s["sleeve"] == BENCH_SLEEVE[ccy], "weight"] = 1 - w
    return s[s["portfolio"] == ccy]


def run_scenarios(moves):
    rows = []
    for name, sc in SCENARIOS.items():
        if sc["rate"] is None:
            if moves is None:
                continue
            rate = dict(zip(moves["currency"], moves["change_bp"]))
        else:
            rate = sc["rate"]
        s = reprice(SLEEVES, rate, sc["spread"])
        s.insert(0, "scenario", name)
        s["scenario_input_type"] = sc["input_type"]
        rows.append(s)
    return pd.concat(rows, ignore_index=True)


def run_sensitivities():
    out = []
    for ccy in ["USD", "CAD", "EUR"]:
        scen = [COMMON, RISKOFF] if ccy == "EUR" else [COMMON]
        for name in scen:
            for w in MIX_WEIGHTS:
                g = summarize(reprice(with_mix(ccy, w), SCENARIOS[name]["rate"], SCENARIOS[name]["spread"]))
                out.append(dict(test="composition", scenario=name, portfolio=ccy,
                                parameter=f"{SPREAD_SLEEVE[ccy]} weight (complement {BENCH_SLEEVE[ccy]})",
                                value=w, **g.iloc[0][["rate_pct", "spread_pct", "net_pct"]].to_dict()))
    for d in SPREAD_DURATIONS:
        s = SLEEVES.copy()
        s.loc[s["d_spread"] > 0, "d_spread"] = d   # government sleeves stay at 0; weights and d_rate fixed
        g = summarize(reprice(s, SCENARIOS[COMMON]["rate"], SCENARIOS[COMMON]["spread"]))
        for _, r in g.iterrows():
            out.append(dict(test="spread duration", scenario=COMMON, portfolio=r["portfolio"],
                            parameter="spread duration of spread-bearing sleeves (years)", value=d,
                            rate_pct=r["rate_pct"], spread_pct=r["spread_pct"], net_pct=r["net_pct"]))
    df = pd.DataFrame(out)
    df["input_type"] = "illustrative"
    return df


# ================================================================= checks
def validate(contrib, summary, sens, moves, monthly, hist_err):
    checks = []

    def add(name, ok, detail=""):
        checks.append(dict(check=name, result="PASS" if ok else "FAIL", detail=detail))

    w = SLEEVES.groupby("portfolio")["weight"].sum()
    add("weights sum to one per portfolio (base)", bool((w - 1).abs().max() < TOL), w.to_dict())
    mix_ok = all(abs(with_mix(c, x)["weight"].sum() - 1) < TOL for c in SPREAD_SLEEVE for x in MIX_WEIGHTS)
    add("weights sum to one per portfolio (composition variants)", mix_ok)

    z = summarize(reprice(SLEEVES, dict(USD=0, CAD=0, EUR=0), {k: 0 for k in SLEEVES["sleeve"]}))
    add("zero shock produces zero P&L", bool(z["total_contrib"].abs().max() == 0))
    add("100 bp equals 0.01 in decimal yield", 100 / 10_000 == 0.01)

    up_r = reprice(SLEEVES, dict(USD=1, CAD=1, EUR=1), {k: 0 for k in SLEEVES["sleeve"]})
    up_s = reprice(SLEEVES, dict(USD=0, CAD=0, EUR=0), {k: 1 for k in SLEEVES["sleeve"]})
    add("higher benchmark rate lowers value (all sleeves, D_rate>0)", bool((up_r["total_contrib"] < 0).all()))
    add("higher spread lowers value (sleeves with D_spread>0); zero for government",
        bool((up_s.loc[up_s["d_spread"] > 0, "total_contrib"] < 0).all()
             and (up_s.loc[up_s["d_spread"] == 0, "total_contrib"] == 0).all()))

    def get(sc, ccy, col):
        return float(summary.query("scenario == @sc and portfolio == @ccy")[col].iloc[0])

    for ccy, exp in dict(USD=-0.90, CAD=-0.90, EUR=-3.00).items():
        got = get(COMMON, ccy, "net_pct")
        add(f"base common widening {ccy} = {exp:+.2f}%", abs(got - exp) < 1e-9, f"{got:+.6f}%")
    for ccy, (r, sp, n) in dict(USD=(3.0, -0.9, 2.1), CAD=(3.0, -0.9, 2.1), EUR=(3.0, -5.4, -2.4)).items():
        got = (get(RISKOFF, ccy, "rate_pct"), get(RISKOFF, ccy, "spread_pct"), get(RISKOFF, ccy, "net_pct"))
        ok = all(abs(a - b) < 1e-9 for a, b in zip(got, (r, sp, n)))
        add(f"base risk-off {ccy}: rate {r:+.2f}%, spread {sp:+.2f}%, net {n:+.2f}%", ok,
            "got " + ", ".join(f"{x:+.6f}%" for x in got))

    rec = contrib.groupby(["scenario", "portfolio"], sort=False)["total_contrib"].sum().reset_index()
    m = rec.merge(summary, on=["scenario", "portfolio"], suffixes=("_sleeves", ""))
    err1 = (m["total_contrib_sleeves"] - (m["rate_contrib"] + m["spread_contrib"])).abs().max()
    err2 = (contrib["total_contrib"] - contrib["rate_contrib"] - contrib["spread_contrib"]).abs().max()
    add("contributions reconcile before rounding (sleeve and portfolio)", max(err1, err2) < TOL,
        f"max abs error {max(err1, err2):.2e}")

    eur_common = sens.query("test == 'composition' and portfolio == 'EUR' and scenario == @COMMON")["net_pct"]
    swapped = SLEEVES.copy()
    swapped.loc[swapped["sleeve"] == "sp", "weight"], swapped.loc[swapped["sleeve"] == "snp", "weight"] = 0.30, 0.70
    sw = summarize(reprice(swapped, SCENARIOS[COMMON]["rate"], SCENARIOS[COMMON]["spread"]))
    sw_eur = float(sw.query("portfolio == 'EUR'")["net_pct"].iloc[0])
    add("EUR common stress flat across seniority mix and sleeve swap",
        bool(eur_common.sub(-3.0).abs().max() < 1e-9 and abs(sw_eur + 3.0) < 1e-9),
        f"mix 10/30/60%: {list(eur_common.round(10))}; swapped 30/70: {sw_eur:+.6f}%")

    if hist_err:
        add("historical case: three validated series, 12 monthly common observations", False,
            f"BLOCKER - historical case omitted: {hist_err}")
    else:
        add("historical case: three validated series, 12 monthly common observations",
            len(monthly) == 12 and moves["change_bp"].notna().all(),
            f"Jan {moves['jan_common_date'].iloc[0]} -> Dec {moves['dec_common_date'].iloc[0]}")
    add("no cross-currency P&L aggregation", "ALL" not in set(summary["portfolio"]))
    return pd.DataFrame(checks)


# ================================================================= chart
def chart(summary, path):
    d = summary[summary["scenario"] == RISKOFF].set_index("portfolio").loc[["USD", "CAD", "EUR"]]
    blue, orange, ink, muted, grid = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e4e3df"
    fig, ax = plt.subplots(figsize=(8.2, 4.0), dpi=150)
    y = range(len(d))[::-1]
    h = 0.5
    ax.barh(y, d["rate_pct"], height=h, color=blue, label="Rate contribution (benchmark -50 bp)",
            edgecolor="white", linewidth=1)
    ax.barh(y, d["spread_pct"], height=h, color=orange, label="Spread contribution", edgecolor="white", linewidth=1)
    ax.scatter(d["net_pct"], y, marker="D", s=70, color=ink, zorder=3, label="Net", edgecolors="white", linewidths=1.5)
    for yi, (_, r) in zip(y, d.iterrows()):
        ax.text(r["rate_pct"] + 0.12, yi, f"{r['rate_pct']:+.2f}%", va="center", ha="left", fontsize=9, color=muted)
        ax.text(r["spread_pct"] - 0.12, yi, f"{r['spread_pct']:+.2f}%", va="center", ha="right", fontsize=9, color=muted)
        ax.text(r["net_pct"], yi + 0.38, f"net {r['net_pct']:+.2f}%", va="center", ha="center", fontsize=9,
                color=ink, fontweight="bold")
    ax.axvline(0, color=muted, linewidth=1)
    ax.set_yticks(list(y))
    ax.set_yticklabels(["USD\nTreasury / state-local", "CAD\nGoC / provincial", "EUR\nFrench bank SP / SNP"],
                       fontsize=9, color=ink)
    ax.set_xlim(-7.2, 4.6)
    ax.set_xlabel("Instantaneous P&L, % of initial portfolio market value (local currency)", fontsize=9, color=muted)
    ax.xaxis.grid(True, color=grid, linewidth=0.8)
    ax.set_axisbelow(True)
    for sp in ["top", "right", "left"]:
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(grid)
    ax.tick_params(colors=muted, labelsize=9, length=0)
    ax.set_title("Risk-off with a bond rally: rate vs spread contributions\n"
                 "Illustrative synthetic portfolios; spreads +50 / +50 / +75 SP / +125 SNP bp",
                 fontsize=10.5, color=ink, loc="left")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.38), ncol=3, frameon=False, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


# ================================================================= report
def fmt(x):
    return f"{x:+.2f}%"


def write_report(path, manifest, moves, summary, sens, checks, hist_err):
    L = ["# Credit Risk Management: Theory and Application", "",
         "## Scenario repricing of three synthetic bond portfolios - results", "",
         "All portfolio figures are instantaneous, first-order mark-to-market changes in local currency, as a "
         "percentage of initial portfolio market value. Benchmark yield changes are observed; holdings, durations "
         "and the two designed stresses are illustrative.", "",
         f"Run on {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `credit_risk_scenarios.py`.", "",
         "## Observed inputs (benchmark yields)", ""]
    for _, r in manifest.iterrows():
        L.append(f"- **{r['currency']}** `{r['series_id']}` ({r['provider']}): **{r['status']}**"
                 + (f" - {r.get('error')}" if r["status"] != "ok" else f", {r['valid_obs']} valid obs "
                    f"{r['first_date']} to {r['last_date']}")
                 + (f". Note: {r['note']}" if isinstance(r.get("note"), str) and r["note"] else ""))
    L.append("")
    if hist_err:
        L += [f"**Historical case omitted.** Blocker: {hist_err}. The designed stresses, sensitivities and "
              "model checks still ran; the historical-data check is reported as FAIL.", ""]
    else:
        L += [f"### {HIST}", "", "| Currency | Jan common date | Jan yield % | Dec common date | Dec yield % | Change bp |",
              "|---|---|---:|---|---:|---:|"]
        for _, r in moves.iterrows():
            L.append(f"| {r['currency']} | {r['jan_common_date']} | {r['jan_yield_pct']:.4f} | {r['dec_common_date']} "
                     f"| {r['dec_yield_pct']:.4f} | {r['change_bp']:+.2f} |")
        L += ["", "These yield changes are observed. Early-January moves before the January month-end common date "
              "are excluded by construction.", "",
              "Resulting rate-only repricing (illustrative: observed move x synthetic 6-year benchmark duration, "
              "spreads held at 0):", "", "| Portfolio | Rate shock bp | Rate contribution | Net |", "|---|---:|---:|---:|"]
        for _, r in summary[summary["scenario"] == HIST].iterrows():
            bp = float(moves.set_index("currency").loc[r["portfolio"], "change_bp"])
            L.append(f"| {r['portfolio']} | {bp:+.2f} | {fmt(r['rate_pct'])} | {fmt(r['net_pct'])} |")
        L.append("")
    L += ["## Illustrative portfolio results (% of initial MV, local currency)", "",
          "| Scenario | Portfolio | Rate | Spread | Net |", "|---|---|---:|---:|---:|"]
    for _, r in summary.iterrows():
        L.append(f"| {r['scenario']} | {r['portfolio']} | {fmt(r['rate_pct'])} | {fmt(r['spread_pct'])} | {fmt(r['net_pct'])} |")
    L += ["", "### Sensitivities", "", "| Test | Scenario | Portfolio | Value | Net |", "|---|---|---|---:|---:|"]
    for _, r in sens.iterrows():
        v = f"{r['value']:.0%}" if r["test"] == "composition" else f"{r['value']:.0f}y"
        L.append(f"| {r['test']} | {r['scenario']} | {r['portfolio']} | {v} | {fmt(r['net_pct'])} |")
    L += ["", "## Validation", "", "| Check | Result | Detail |", "|---|---|---|"]
    for _, r in checks.iterrows():
        L.append(f"| {r['check']} | {r['result']} | {str(r['detail']).replace('|', '/')} |")
    L += ["", "## Interpretation", "",
          "- **Common spread widening isolates spread-bearing weights.** Every spread-bearing sleeve gets the same "
          "+50 bp at the same 6-year spread duration and benchmarks do not move, so the only thing that differs "
          "across portfolios is the share of spread-bearing holdings: 30% in USD and CAD (-0.90%) versus 100% in EUR "
          "(-3.00%). That is why the EUR result is unchanged by the SP/SNP mix. It is a property of the synthetic "
          "design, not evidence about real portfolios.",
          "- **Risk-off also changes the shocks themselves.** The -50 bp benchmark rally adds +3.00% everywhere, but "
          "the assumed spread shocks now differ by sleeve (+50 bp state/local and provincial, +75 bp SP, +125 bp SNP). "
          "The EUR loss therefore reflects both its 100% spread-bearing weight and the larger hypothesised bank "
          "spread moves; the two effects are not separated by this scenario. Moving SNP weight from 10% to 60% "
          "takes EUR from -1.80% to -3.30% net.",
          "- Spread-duration sensitivity scales spread losses linearly: going from 4 to 8 years doubles them.",
          "- The 2022 case is rate-only: it reprices the benchmark sleeves for the observed 10-year moves with no "
          "spread move, so it says nothing about 2022 credit-spread behaviour.", "",
          "## Limitations", "",
          "- Synthetic weights and durations; no Banque de France holdings or sensitivities.",
          "- First-order duration only: no convexity, carry, roll-down, default losses or FX. Same pre-shock weights.",
          "- Designed stresses carry no historical frequency or empirical calibration.",
          "- Benchmark series differ in instrument and compounding (CMT par yield, benchmark bond yield, AAA "
          "spot rate); only within-series changes are used. The EUR AAA curve is a proxy, not an OIS or OAT curve.",
          "- The 10-year move is applied as a parallel shock to a 6-year-duration portfolio.",
          "- No cross-currency aggregation and no ranking of actual portfolios."]
    path.write_text("\n".join(L) + "\n")


# ================================================================= main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--usd-csv"), ap.add_argument("--cad-csv"), ap.add_argument("--eur-csv")
    ap.add_argument("--cad-series", default=SOURCES["CAD"]["series_id"],
                    help="Bank of Canada Valet series id (default BD.CDN.10YR.DQ.YLD; V39055 from the project brief returns 404)")
    ap.add_argument("--offline", action="store_true", help="do not download; use only local CSVs")
    args = ap.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    series, manifest = load_benchmarks(args, outdir)
    moves, monthly, hist_err = historical_moves(series)
    if moves is not None:
        moves.insert(1, "series_id", moves["currency"].map(manifest.set_index("currency")["series_id"]))

    contrib = run_scenarios(moves)
    summary = pd.concat([summarize(g).assign(scenario=n) for n, g in contrib.groupby("scenario", sort=False)])
    summary = summary[["scenario", "portfolio", "rate_contrib", "spread_contrib", "total_contrib",
                       "rate_pct", "spread_pct", "net_pct"]]
    summary["input_type"] = summary["scenario"].map({k: v["input_type"] for k, v in SCENARIOS.items()})
    sens = run_sensitivities()
    checks = validate(contrib, summary, sens, moves, monthly, hist_err)

    for c in ["rows_raw", "unparseable_dates", "missing_markers_dropped", "valid_obs"]:
        if c in manifest:
            manifest[c] = manifest[c].astype("Int64")
    manifest.to_csv(outdir / "source_manifest.csv", index=False)
    for stale in ["benchmark_levels_clean.csv", "benchmark_monthly_common.csv", "historical_benchmark_moves.csv"]:
        (outdir / stale).unlink(missing_ok=True)   # never leave results from an earlier run
    if series:
        levels = pd.concat(series.values(), ignore_index=True)
        common = set.intersection(*(set(s["date"]) for s in series.values()))
        levels["common_valid_date"] = levels["date"].isin(common) if len(series) == len(SOURCES) else False
        levels["month_end_common_date"] = levels["date"].isin(monthly.index) if moves is not None else False
        levels["date"] = levels["date"].dt.strftime("%Y-%m-%d")
        levels.to_csv(outdir / "benchmark_levels_clean.csv", index=False)
    if monthly is not None:
        mo = monthly.copy()
        mo.index = mo.index.strftime("%Y-%m-%d")
        mo.rename_axis("common_month_end_date").to_csv(outdir / "benchmark_monthly_common.csv")
    if moves is not None:
        moves.to_csv(outdir / "historical_benchmark_moves.csv", index=False)
    pd.concat([SLEEVES.assign(table="sleeves"),
               pd.DataFrame([dict(table="scenarios", scenario=k, rate_shock_bp=json.dumps(v["rate"]) if v["rate"]
                                  else "observed Jan-Dec month-end change", spread_shock_bp=json.dumps(v["spread"]),
                                  input_type=v["input_type"]) for k, v in SCENARIOS.items()])],
              ignore_index=True).to_csv(outdir / "assumptions_inputs.csv", index=False)
    contrib.to_csv(outdir / "sleeve_contributions.csv", index=False)
    summary.to_csv(outdir / "portfolio_summary.csv", index=False)
    sens.to_csv(outdir / "sensitivity_composition_duration.csv", index=False)
    checks.to_csv(outdir / "validation_checks.csv", index=False)
    chart(summary, outdir / "risk_off_contributions.png")
    write_report(outdir / "RESULTS.md", manifest, moves, summary, sens, checks, hist_err)

    pd.set_option("display.width", 160)
    print(manifest[["currency", "series_id", "status"] + (["error"] if "error" in manifest else [])].to_string(index=False))
    if moves is not None:
        print("\n" + moves.to_string(index=False))
    else:
        print(f"\nHistorical case omitted: {hist_err}")
    print("\n" + summary[["scenario", "portfolio", "rate_pct", "spread_pct", "net_pct"]].round(4).to_string(index=False))
    print("\n" + checks[["check", "result"]].to_string(index=False))
    print(f"\nOutputs written to {outdir}/")
    failed = checks[checks["result"] == "FAIL"]["check"]
    if failed.empty:
        return 0
    return 2 if list(failed) == ["historical case: three validated series, 12 monthly common observations"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
