"""Live vs recommendation report for the 1-year backtests in 1Y_back.

Builds the same HTML report as the margin dashboard's "compare with live backtests"
button (the template is read from index.html, so the two stay in step), from:

  1Y_back/<INDEX>/recommendation/<Weekday>/   the dashboard's picks, run for a year
  1Y_back/<INDEX>/current_live/<backtest>/    the live strategies, run for a year

Recommended book, per pick (same scaling as the dashboard's performance section):
  daily P&L ($) = raw daily P&L (₹) × tranche factor × P&L scale ÷ USD/INR
  tranche factor = baseline margin ÷ pick margin   (the factor applied when the
                   margin CSVs were built; brings the pick to margin @0DTE)
  P&L scale      = allocated peak-day margin ÷ margin @0DTE   (from the solve)

Live book, per backtest: raw daily P&L (₹) × live P&L scale ÷ USD/INR, with its margin
worked out from its blotters by the live margin rule, as the dashboard does.

Daily P&L = last portfolio_value of the day − last portfolio_value of the previous
trading day (start 0), from consolidated_store/YYYYMMDD.csv.

Usage:  python build_1y_report.py [--root 1Y_back] [--out report.html]
"""
import argparse
import csv
import datetime as dt
import glob
import json
import os
import re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..', '1Y_back'))

USD_INR = 95.2
CAPITAL = 14_000_000
EXPIRY = {'NF': 'tue', 'SN': 'thu'}
MARGIN_0DTE = {'NF': 1_200_000, 'SN': 1_000_000}
INDEX_DIR = {'NF': 'NIFTY', 'SN': 'SENSEX'}
WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri']

# Live margin rule per index (dashboard default): `unit` contracts every `interval` s,
# held to expiry, needs `margin` dollars. Unwound before expiry: half.
LIVE_RULE = {'NF': {'unit': 260, 'interval': 300, 'margin': 1_200_000},
             'SN': {'unit': 40, 'interval': 180, 'margin': 1_000_000}}
# Multiplier on a live backtest's daily P&L (1 when absent).
LIVE_SCALES = {'NIFTY 2_50000_gammaiv': 0.5}

# The picks: (name, index, run folder, lots, baseline margin, pick margin, allocated peak-day margin $).
# Margins in contracts, from the tranche table. Peak-day margin from the dashboard solve
# (capital $14M, "Expiry 0DTE, other days ½"); 0 = not run.
PICKS = [
    ('NIFTY_MON',  'NF', 'Monday',    '11000', 19_500, 7_020,  0),
    ('NIFTY_TUE',  'NF', 'Tuesday',   '01000', 17_420, 2_340,  0),
    ('NIFTY_WED',  'NF', 'Wednesday', '11111', 19_500, 19_500, 7_000_000),
    ('NIFTY_THU',  'NF', 'Thursday',  '11011', 19_500, 19_500, 0),
    ('NIFTY_FRI',  'NF', 'Friday',    '00001', 19_500, 4_290,  3_500_000),
    ('SENSEX_MON', 'SN', 'Monday',    '11000', 5_000,  1_300,  7_000_000),
    ('SENSEX_TUE', 'SN', 'Tuesday',   '01000', 5_000,  700,    0),
    ('SENSEX_WED', 'SN', 'Wednesday', '00110', 5_000,  5_000,  7_000_000),
    ('SENSEX_THU', 'SN', 'Thursday',  '00010', 4_440,  3_800,  0),
    ('SENSEX_FRI', 'SN', 'Friday',    '00001', 5_000,  1_500,  7_000_000),
]


def fmt(v):
    return ('-' if v < 0 else '') + '$' + f'{abs(round(v)):,}'


def iso(yyyymmdd):
    return f'{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}'


def last_portfolio_value(path):
    with open(path, newline='') as f:
        rows = list(csv.DictReader(f))
    for r in reversed(rows):
        v = (r.get('portfolio_value') or '').strip()
        try:
            return float(v)
        except ValueError:
            continue
    return None


def daily_pnl(run_dir):
    """{date: P&L in ₹} from the run's consolidated_store."""
    closes = []
    for p in glob.glob(os.path.join(run_dir, 'consolidated_store', '*.csv')):
        m = re.match(r'(\d{8})\.csv$', os.path.basename(p))
        c = last_portfolio_value(p) if m else None
        if c is not None:
            closes.append((iso(m.group(1)), c))
    pnl, prev = {}, 0.0
    for d, c in sorted(closes):
        pnl[d] = c - prev
        prev = c
    return pnl


def mode(values):
    """Most common value; first seen wins a tie (as the dashboard's mode())."""
    return Counter(values).most_common(1)[0][0] if values else None


def live_info(run_dir):
    """Per entry weekday, the usual size and gap between 'trade' rows; early unwind flag."""
    per_day = {}
    for p in sorted(glob.glob(os.path.join(run_dir, 'blotter', '*.csv'))):
        m = re.match(r'[^/]*?(\d{8})\d*_[^/]*\.csv$', os.path.basename(p))
        if not m:
            continue
        with open(p, newline='') as f:
            rows = [r for r in csv.DictReader(f) if (r.get('trade_sub_type') or '').strip() == 'trade']
        if not rows:
            continue
        sizes = [abs(float(r['position'])) for r in rows]
        times = sorted({dt.datetime.fromisoformat(r['timestamp'].strip()) for r in rows})
        gap = mode([round((b - a).total_seconds()) for a, b in zip(times, times[1:])])
        wi = dt.date.fromisoformat(iso(m.group(1))).weekday()
        if wi < 5:
            per_day.setdefault(WEEKDAYS[wi], []).append((mode(sizes), gap))
    days = {wd: dict(zip(('size', 'interval'), mode(lst))) for wd, lst in per_day.items()}
    early = None
    cfg = os.path.join(run_dir, 'tradelib_global_constants.py')
    if os.path.exists(cfg):
        u = re.search(r'^unwind_trading_days_before\s*=\s*(\d+)', open(cfg).read(), re.M)
        if u:
            early = int(u.group(1)) > 0
    return {'days': days, 'early': early}


def live_margin(index, info):
    """Margin a live backtest ran at: sum over entry weekdays of the rule margin scaled
    by size and interval, halved when unwound before expiry."""
    rule = LIVE_RULE[index]
    parts = []
    for wd in WEEKDAYS:
        d = info['days'].get(wd)
        if not d:
            continue
        m = rule['margin'] * (d['size'] / rule['unit']) * (rule['interval'] / d['interval']) * (0.5 if info['early'] else 1) \
            if d['interval'] else 0
        parts.append((wd, d['size'], d['interval'], m))
    return sum(p[3] for p in parts), parts


def peak_unit_margin(index, lots):
    """Peak-day margin of one dial in "Expiry 0DTE, other days ½": m0/2 × lots, ×2 on expiry."""
    m0 = MARGIN_0DTE[index]
    return max(m0 / 2 * int(lots[i]) * (2 if wd == EXPIRY[index] else 1) for i, wd in enumerate(WEEKDAYS))


def build(root):
    # ---- recommended book ----
    rec = []
    for name, ix, day, lots, base_m, pick_m, peak in PICKS:
        run = os.path.join(root, INDEX_DIR[ix], 'recommendation', day)
        raw = daily_pnl(run)
        factor = base_m / pick_m
        m0 = MARGIN_0DTE[ix]
        pnl_scale = peak / m0
        dollars = {d: v * factor / USD_INR for d, v in raw.items()}  # the margin CSV's daily_pnl, in $
        rec.append(dict(name=name, index=ix, lots=lots, factor=factor, m0=m0, peak=peak, pnl_scale=pnl_scale,
                        dial=peak / peak_unit_margin(ix, lots), raw=raw, daily=dollars))
    rec_dates = sorted({d for r in rec for d in r['daily']})
    dash_series = {r['name']: [r['pnl_scale'] * r['daily'].get(d, 0) for d in rec_dates] for r in rec if r['pnl_scale'] > 0}
    dash_combined = [sum(v[j] for v in dash_series.values()) for j in range(len(rec_dates))]

    # ---- live book ----
    live = []
    for ix in ('NF', 'SN'):
        for run in sorted(glob.glob(os.path.join(root, INDEX_DIR[ix], 'current_live', '*', ''))):
            name = INDEX_DIR[ix] + ' ' + os.path.basename(os.path.normpath(run))
            k = LIVE_SCALES.get(name, 1)
            info = live_info(run)
            margin, parts = live_margin(ix, info)
            live.append(dict(name=name, label=name if k == 1 else f'{name} (× {k:g})', index=ix, k=k,
                             raw=daily_pnl(run), margin=margin, parts=parts, early=info['early']))
    live_dates = sorted({d for l in live for d in l['raw']})
    live_series = {l['label']: [l['raw'].get(d, 0) * l['k'] / USD_INR for d in live_dates] for l in live}
    live_combined = [sum(v[j] for v in live_series.values()) for j in range(len(live_dates))]

    # ---- by-underlying books (rescalable in the report) ----
    underlying = []
    for l in live:
        note = ', '.join(f'{wd.capitalize()} {s:g} @ {i}s' for wd, s, i, _ in l['parts']) + \
               (', unwound early (½)' if l['early'] else ', held to expiry')
        underlying.append(dict(source='live', index=l['index'], name=l['label'], dates=live_dates,
                               pnl=live_series[l['label']], base=l['margin'] * l['k'], note=note))
    for r in rec:
        underlying.append(dict(source='rec', index=r['index'], name=r['name'], dates=rec_dates, base=r['pnl_scale'] * r['m0'],
                               perDollar=[r['daily'].get(d, 0) / r['m0'] for d in rec_dates],
                               note=f"lots {r['lots']}, margin @0DTE {fmt(r['m0'])}, tranche factor {r['factor']:.4f}"))

    allocated = ', '.join(f"{r['name']} {fmt(r['peak'])}" for r in rec if r['peak'] > 0)
    config = [
        ['Backtests', '1-year runs in ' + root],
        ['Recommended period', f'{rec_dates[0]} → {rec_dates[-1]}'],
        ['Live period', f'{live_dates[0]} → {live_dates[-1]}'],
        ['Allocation (peak-day margin)', allocated + ' — from the dashboard solve; other picks not run'],
        ['Margin mode', 'Expiry 0DTE, other days ½'],
        ['Total margin capital', fmt(CAPITAL)],
        ['Expiry weekdays', 'NF tue, SN thu'],
        ['Recommended P&L', 'raw ₹ × tranche factor (baseline ÷ pick margin) × P&L scale (peak-day margin ÷ margin @0DTE) ÷ USD/INR'],
        ['Live margin rule', '; '.join(f"{INDEX_DIR[ix]} {r['unit']} every {r['interval']}s = {fmt(r['margin'])}" for ix, r in LIVE_RULE.items())],
        ['Live folder', f'current_live ({len(live)} backtests)'],
        ['USD/INR for live P&L', f'₹{USD_INR} per $1'],
        ['Live P&L scales', ', '.join(f'{n} × {k:g}' for n, k in LIVE_SCALES.items()) or 'none (all × 1)'],
    ]
    strat_rows = [[r['name'], r['index'], 'weekly', fmt(r['m0']), r['lots'], 'none', 'none',
                   f"{r['dial']:.4f}", f"{r['pnl_scale']:.4f}", fmt(r['peak'])] for r in rec]
    now = dt.datetime.now()
    data = dict(
        generated=now.strftime('%d/%m/%Y, %H:%M:%S'),
        generatedNote='1-year backtests of the dashboard picks and the current live strategies.',
        usdInr=USD_INR, capital=CAPITAL, liveFolder=os.path.join(os.path.basename(root), '*/current_live'), config=config,
        strategiesTable=dict(headers=['strategy', 'index', 'type', 'margin @0DTE', 'lots', 'min $/strat', 'max $/strat',
                                      'solved dial', 'P&L scale', 'peak-day margin'], rows=strat_rows),
        live=dict(dates=live_dates, series=live_series, combined=live_combined),
        dash=dict(dates=rec_dates, series=dash_series, combined=dash_combined),
        underlying=underlying,
    )
    return data, rec, live


def render(data):
    src = open(os.path.join(HERE, 'index.html'), encoding='utf-8').read()
    m = re.search(r'<script type="text/plain" id="liveReportTpl">\n?(.*?)</script>', src, re.S)
    tpl = m.group(1).replace('<\\/script>', '</script>')
    return tpl.replace('__DATA__', json.dumps(data, ensure_ascii=False).replace('<', '\\u003c'), 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--root', default=DEFAULT_ROOT, help='folder holding NIFTY/ and SENSEX/ (default: %(default)s)')
    ap.add_argument('--out', help='output HTML (default: <root>/margin_vs_live_1Y_<stamp>.html)')
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    data, rec, live = build(root)
    out = a.out or os.path.join(root, 'margin_vs_live_1Y_' + dt.datetime.now().strftime('%Y%m%d-%H%M') + '.html')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(render(data))

    print('Recommended picks (raw ₹ → $ at margin @0DTE → × P&L scale):')
    for r in rec:
        raw = sum(r['raw'].values())
        print(f"  {r['name']:11} raw ₹{raw:>14,.0f}  × {r['factor']:.4f} ÷ {USD_INR} = {fmt(sum(r['daily'].values())):>12}"
              f"  × {r['pnl_scale']:.2f} = {fmt(sum(r['daily'].values()) * r['pnl_scale']):>12}")
    print(f"  combined {fmt(sum(data['dash']['combined']))}")
    print('Live backtests:')
    for l in live:
        print(f"  {l['label']:38} raw ₹{sum(l['raw'].values()):>14,.0f}  → {fmt(sum(l['raw'].values()) * l['k'] / USD_INR):>12}"
              f"  margin {fmt(l['margin'] * l['k'])}")
    print(f"  combined {fmt(sum(data['live']['combined']))}")
    print('Wrote', out)


if __name__ == '__main__':
    main()
