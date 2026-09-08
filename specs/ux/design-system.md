# UX — RouteRadar design system

Source: `specs/ux/routeradar/RouteRadar.dc.html` (pulled 2026-09-09 from the Claude Design
project "RouteRadar CDG Pakistan Dashboard"). It is built on the **Nocturne** system in
`specs/ux/routeradar/_ds/nocturne-*/` — read its `readme.md` for the general rules (outlined
buttons, accent as line and glow never flood, no pure black/white, hierarchy by size not
weight). This file is the project-specific translation: tokens the site copies verbatim,
the page anatomy, and the data each region needs.

## Tokens (copy verbatim into `web/src/tokens.css`)

Nocturne base, from `styles.css`:

```css
--color-bg: #161826;        --color-surface: #232532;   --color-text: #e9e9ed;
--color-accent: #9184d9;    --color-accent-300: #d2cefd; --color-accent-400: #b5abfc;
--color-accent-600: #796cbf; --color-accent-700: #5d5294; --color-accent-800: #423a6a;
--color-neutral-700: #595d6c; --color-neutral-800: #3f424d;
--color-divider: color-mix(in srgb, #e9e9ed 16%, transparent);
--font-body: "Inter", system-ui, sans-serif;   /* weights 400/500/600 */
--font-mono: "JetBrains Mono", monospace;       /* weights 400/500/700 — numbers, codes, dates */
--space-1: 2.8px; --space-2: 5.6px; --space-3: 8.4px; --space-4: 11.2px; --space-6: 16.8px; --space-8: 22.4px;
--radius-sm: 4px; --radius-md: 8px; --radius-lg: 14px;
--shadow-sm: 0 0 0 1px #3f424d;
--shadow-md: 0 0 0 1px #595d6c, 0 6px 18px rgba(0,0,0,0.55);
--shadow-lg: 0 0 0 1px #9397ab, 0 16px 40px rgba(0,0,0,0.65);
```

RouteRadar additions (the design uses these literally):

```css
--color-card: #1b1d2c;                 /* card ground, one step above --color-bg */
--color-ok: #4bb87c;                   /* "updated" dot */
--text-muted-72: rgba(233,233,237,0.72);  --text-muted-55: rgba(233,233,237,0.55);
--text-muted-45: rgba(233,233,237,0.45);  --text-muted-40: rgba(233,233,237,0.40);
/* carriers — one hue each, used for lines, chips, dots, labels */
--carrier-PK: #3fae6d;  /* PIA */        --carrier-QR: #d1487f;  /* Qatar */
--carrier-EK: #e0574a;  /* Emirates */   --carrier-GF: #d6a63c;  /* Gulf Air */
--carrier-TK: #e0435c;  /* Turkish */    --carrier-SV: #37a894;  /* Saudia */
/* calendar bands — background shading behind the curve */
--band-wedding: #d6a63c;  --band-religious: #3fae8d;  --band-french: #6f8fd6;
/* event severity — pins, feed dots, drawer badge */
--sev-high: #e0574a;  --sev-med: #d6a63c;  --sev-low: rgba(233,233,237,0.5);
```

**Known palette problem, to fix in S13:** Emirates `#e0574a` and Turkish `#e0435c` are
near-identical reds, and Emirates also equals `--sev-high`; Gulf Air equals `--band-wedding`
and `--sev-med`. Six carrier hues need to be distinguishable from each other and from the
band/severity roles, and colour-blind safe (run the `dataviz` skill validator). Keep the
ones that are fine (PIA green, Qatar pink, Saudia teal) and move the others.

## Type scale (as used)

| Role | Font | Size | Notes |
|---|---|---|---|
| Brand | Inter 600 | 16px | letter-spacing −0.01em |
| Card title (h4/h5) | Inter 500 | 17px / 15px | never bolder than 500 |
| Kicker | Inter | 10px | uppercase, letter-spacing 0.1em, accent colour |
| Body | Inter 400 | 15px base, 12–13.5px in cards | |
| Numbers, codes, dates | JetBrains Mono | 10–13px; 22px spread; 44px gauge | all figures are mono |
| Axis ticks | JetBrains Mono | 10–10.5px, `--text-muted-40` | |

## Page anatomy (one page, top to bottom)

1. **Header** (sticky, blurred `rgba(22,24,38,0.92)`): brand mark (accent ring), name,
   mono route tag `CDG → PK-NORTH`; constraint pill `◈ Max 1 Stop | Layover ≤ 7h`; status
   `● Updated today 04:00 CET · Daily cron ingest` — the timestamp comes from
   `generated_at`.
2. **Filter bar**: Origin (fixed, display-only `CDG / ORY`), Destination segmented
   control `ISB · LHE · SKT · All`, Carrier chips (toggle, colour dot with glow when on),
   Booking horizon segmented control `14d … 180d`. Selected segment: accent-tinted
   background `rgba(145,132,217,0.16)`, inset 1px accent ring, `--color-accent-300` text.
3. **Main grid** `minmax(0,1fr) 320px`:
   - **Fare chart card**: title "Lowest fare by carrier", mono route, subtitle; 340px plot;
     calendar bands as vertical gradients (`color22 → color0d`, 1px `color40` edges) with
     uppercase labels alternating two rows; y grid lines and `€` ticks; one SVG path per
     carrier (1.6px, non-scaling stroke); event pins as 20px circles on the baseline
     (`!` high, `▲` med, `•` low) with a faint vertical guide; hover = vertical hairline +
     card (290px, 250px under 620px) listing date, calendar tags, carriers sorted by
     price with route and duration, and events within ±5 days. Legend row below.
   - **Event feed** aside (sticky at `top:74px` on wide screens): severity dot, headline,
     `date · source · impact score`, accent impact line; click opens the drawer.
4. **Modules grid** `repeat(auto-fit, minmax(300px,1fr))`, three cards on `--color-card`
   with `--shadow-sm`:
   - **Airport arbitrage**: LHE vs SKT bars, spread `+€NN` in 22px mono, verdict pill
     ("Fly LHE + M-11 transfer" / "Fly SKT direct"), net after a ground-transfer cost.
   - **Carrier efficiency matrix**: scatter of price (y, `€480–€1100`) vs total transit
     (x, `7h–17h`), glow dots in carrier colours, "Sweet spot" quadrant shading bottom-left.
   - **Seasonal multiplier**: `+NN%` in 44px mono wedding-gold, gradient gauge, current
     average vs February baseline, one-line method note.
5. **Event drawer**: right-side panel `min(430px, 92vw)`, backdrop blur, severity badge,
   date, headline (22px), body, and a Source / Est. fare impact / Scope grid.

## Responsive

Breakpoints from the design: `< 900px` the aside drops below the chart and the plot
becomes horizontally scrollable at `min-width: 760px`; `< 620px` paddings shrink to 12px,
the hover card narrows to 250px, module columns go to 260px minimum. Touch: hover becomes
a tap that sticks until the next tap; pins and feed rows are already ≥ 20px, keep targets
≥ 44px by padding the hit area, not the glyph.

## Data the page needs (drives the JSON contract, S14)

Read straight off the design's `data-dc-script`:

| Region | Fields |
|---|---|
| Header | `generated_at`, `observed_on` |
| Carriers | `code, name, color, hub, direct, typical_transit_hours` |
| Destinations | `code, label` (`ALL` is computed client-side) |
| Horizons | `[14, 30, 60, 90, 120, 180]` |
| Series | per `(destination, horizon, carrier)`: `[[departure_date, price_eur], …]`, ~3-day step over the next 11 months; the page computes the y-range |
| Bands | `label, short_label, from, to, kind (wedding/religious/french), tag, multiplier` |
| Events | `date, severity (high/med/low), headline, source, impact_text, body, impact_score` |
| Arbitrage | `lhe_eur, skt_eur, isb_eur, spread_eur, ground_transfer_eur (34), ground_time ("4h 10m")`, verdict computed client-side |
| Efficiency | per carrier: `price_eur, transit_hours` on the sampled date |
| Seasonal gauge | `current_avg_eur, baseline_avg_eur, pct` |

The design's fake `fare()` model (band multipliers ×, event decay `exp(-gap/9)` over 27
days) is illustration only; real values come from DuckDB. Its multipliers (wedding 1.34,
Toussaint 1.11, Ramadan 0.92, Eid-ul-Fitr 1.27, Hajj 1.21, French summer 1.26) are a
reasonable first setting for `calendar_engine` and are within the brainstorm's ranges.
Note it also includes **Toussaint**, which the brainstorm table did not; add it to S05.
