# Margin Allocation Desk

A single self-contained web app for planning short-vol margin allocation across
strategies: DTE-curve or independent margin input, expiry-day doubling, weekly/
monthly horizons, min/max margin floors & ceilings, and a client-side MILP
solver for optimal allocation.

Everything — HTML, CSS, and JS — lives in `index.html`. There is no backend,
no build step, and no install. It calls out to two CDNs at load time for the
solver and Excel libraries:

- `https://cdn.jsdelivr.net/npm/javascript-lp-solver@1.0.3/dist/solver.global.js`
- `https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js`

so you need an internet connection the first time each library loads (your
browser will cache them after that). No other network calls are made — all
your data stays in your browser's local storage, on your machine.

## Running it

**Easiest — just open the file:**
Double-click `index.html`, or drag it into a browser tab. That's it.

**Or serve it locally** (only needed if your browser is picky about
`file://` pages, which is rare for this app since it makes no local file
requests):

```bash
# Python 3 (built in on most systems)
cd margin-desk-package
python3 -m http.server 8000
# then open http://localhost:8000 in your browser
```

```bash
# Node, if you have it
npx http-server -p 8000
```

## Sharing it

Send the whole folder (or just `index.html` — the README isn't required to
run it) to anyone. They open it the same way — no accounts, no setup, no
dependencies to install on their end either.

## Data & privacy

All strategy data, capital settings, and preferences are stored in your
browser's `localStorage`, scoped to wherever you're opening the file from
(so `file://.../index.html` and `http://localhost:8000` are treated as
different storage buckets by the browser — if you switch how you open it,
you'll start with an empty strategy table again). Nothing is sent anywhere
except the two CDN library loads above.

## Downloading a sample Excel template

The app itself has a "download sample .xlsx" button on the Strategies panel
that generates a template with the right column headers — use that rather
than building one by hand.
