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
/* carriers — one hue each, used for lines, chips, dots, labels.
   Replaced wholesale in S13; see § Carrier palette. RouteRadar.dc.html still carries
   the old values and is not hand-edited, so these are the ones the site copies. */
--carrier-PK: #53a000;  /* PIA */        --carrier-QR: #f33a87;  /* Qatar */
--carrier-EK: #b44900;  /* Emirates */   --carrier-GF: #9d3ebf;  /* Gulf Air */
--carrier-TK: #0565fc;  /* Turkish */    --carrier-SV: #1d93a6;  /* Saudia */
/* calendar bands — background shading behind the curve */
--band-wedding: #d6a63c;  --band-religious: #3fae8d;  --band-french: #6f8fd6;
/* event severity — pins, feed dots, drawer badge */
--sev-high: #e0574a;  --sev-med: #d6a63c;  --sev-low: rgba(233,233,237,0.5);
```


## Carrier palette

Set in S13. The six carrier hues are the only colour on the page that carries data
identity, so they are computed and validated rather than chosen: the `dataviz` skill's
`validate_palette.js` is the authority and `tests/test_design_system.py` re-runs the same
checks against the tokens above, so a hand-edit that breaks them fails `scripts/check.sh`.

**Run it against the fare card's ground, `--color-card`, on the all-pairs list.** The
validator is not in this repo: it ships with the `dataviz` skill, so invoke the skill and
run the script from the base directory it prints.

```
node <dataviz-skill>/scripts/validate_palette.js \
     "#53a000,#1d93a6,#0565fc,#9d3ebf,#f33a87,#b44900" \
     --mode dark --surface "#1b1d2c" --pairs all
```

`--pairs all` rather than the default adjacent list because both places a carrier colour
appears put arbitrary pairs side by side: six overlapping paths in one plot, and six dots
in the efficiency scatter. Result on 2026-09-09 — all five computable checks PASS, worst
all-pairs ΔE 8.2 under deuteranopia (`#b44900`↔`#53a000`) and 18.9 under normal vision
(`#1d93a6`↔`#53a000`), every slot inside the dark band L 0.48–0.67, C ≥ 0.10, contrast
≥ 3:1.

### The rule, by rendering channel

A carrier hue must clear:

| Against | Threshold | Why that number |
|---|---|---|
| another carrier | the skill's gates: all-pairs ΔE ≥ 8 protan/deutan, ≥ 15 normal | same channel, same plot — a reader tells two carriers apart by colour alone |
| `--color-accent` `#9184d9` | ΔE ≥ 15 normal | the accent is chrome (selected chips, the feed's impact line, the hover-card ring); a line that reads as chrome is the one confusion context cannot resolve |
| `--sev-high`, `--sev-med` | ΔE ≥ 10 normal | severity pins are 20px solid circles on the baseline carrying a glyph (`!` `▲` `•`); shape and glyph carry severity, colour only reinforces it |
| `--band-*`, `--color-ok` | ΔE ≥ 10 normal, and never equal | bands render at 13–21 % alpha behind the curve and `--color-ok` is a 6px header dot, so the compared thing is a wash, not the token |

**Why the thresholds are not all 15.** A six-hue set that clears ΔE ≥ 15 against every role
colour does exist — `#9c7c0b,#2b774d,#1464fe,#955489,#b50fcb,#f046b2` is one, and the
validator passes it. It costs two things.

It lands exactly on both floors that matter: all-pairs ΔE 8.1 protan and 15.0 normal against
targets of 8 and 15, where the shipped set has 8.2 and 18.9.

And it gives up Saudia's hue. `--band-religious` and `--band-french` box in the cyan arc, and
the ceiling there is a function of hue — scanning the whole dark band (L 0.48–0.67, C ≥ 0.10,
contrast ≥ 3:1 on `--color-card`), the best any colour at that hue can do against its nearest
role colour is:

| H | 195 | 205 | 215 | 220 | 225 | **226** |
|---|---|---|---|---|---|---|
| best min ΔE to a role colour | 10.2 | 11.2 | 12.8 | 13.4 | 14.3 | **15.0** |

So the arc does clear 15 — but only past H ≈ 226, which is a cyan-*blue*, not a teal.
`--carrier-SV` sits at H 212, and at Saudia's own hue family (H 195–215) the ceiling is
10.2–12.8. The one colour that clears the arc, `#017194`, also fails CVD against the
counter-example set it would have to join (ΔE 3.7 protan vs `#955489`).

Per-channel thresholds buy back the margin and the three brand hue families; the channels are
what justify them.

Six carriers is near the ceiling either way. A seventh is not a palette change but a design
change — fold to "Other", or facet.

### Per-carrier rationale

| Token | Value | Why |
|---|---|---|
| `--carrier-PK` | `#53a000` | PIA's green, pushed yellow-green: the old `#3fae6d` sat ΔE 4.4 from `--band-religious` and 3.3 from `--color-ok`, so the PIA line vanished into the Ramadan/Eid band and the header dot |
| `--carrier-SV` | `#1d93a6` | Saudia's teal, pushed to cyan: `#37a894` was ΔE 2.4 from `--band-religious` — the two were the same colour to any reader |
| `--carrier-QR` | `#f33a87` | Qatar's burgundy-rose, brightened; the hue survives, but `#d1487f` was ΔE 5.7 from Turkish and 9.3 from `--sev-high` |
| `--carrier-TK` | `#0565fc` | moved off red entirely — `#e0435c` was ΔE 3.3 from Emirates under deuteranopia, the pair that made the chart unreadable. Blue is Turkish's own secondary, and it is the slot the CVD maths demands: it cannot be softened toward periwinkle without collapsing into Gulf Air |
| `--carrier-GF` | `#9d3ebf` | moved off gold — `#d6a63c` *was* `--band-wedding` and `--sev-med`, byte for byte. Gulf Air has no strong second brand colour to honour, so it takes the free violet slot |
| `--carrier-EK` | `#b44900` | moved off red — `#e0574a` *was* `--sev-high`, byte for byte. Burnt orange is the only warm the calendar bands leave open and keeps the red-gold livery's warmth |

Three gates have almost no slack and are the ones a nudge breaks first: `--carrier-SV`
`#1d93a6` sits at C 0.1001 against the 0.10 chroma floor and ΔE 15.06 from `--color-accent`
against the 15 floor, and `--carrier-EK`↔`--carrier-PK` is ΔE 8.2 under deuteranopia against
the target of 8. Re-run the validator after any change to those three; the recorded run
above reports only the worst all-pairs figures and will not warn you.

### Obligations that come with it

- **Identity is never colour alone.** Two things carry a carrier's name next to its colour:
  the filter bar's chips (colour dot + IATA code, always visible, and one tap isolates a
  carrier) and the hover card, which lists carriers by name sorted by price. Both are
  load-bearing, not decoration — do not drop them to save space on phones.
- **The legend row does not legend the carriers.** As drawn
  (`RouteRadar.dc.html:136–141`) it keys the three calendar bands and the news pin and
  names no carrier at all, so with the chips scrolled out of view the plot has no
  colour→carrier key. S15 extends that row with six line keys, one per carrier.
- **Colour follows the carrier, never its rank.** Turning a chip off must not repaint the
  survivors: `--carrier-<code>` is keyed by IATA code, and the export carries `color` per
  carrier so the page never assigns by array position.
- Tritan separation is low (worst pair ΔE 4.1) and is not gated by the skill; the chips, the
  hover card and the added carrier legend are what cover it.


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
   control `ISB · LHE · SKT · All`, Carrier chips (toggle, colour dot with glow when on).
   Selected segment: accent-tinted background `rgba(145,132,217,0.16)`, inset 1px accent
   ring, `--color-accent-300` text. The design also draws a Booking horizon segmented
   control `14d … 180d`; the site **does not build it** (decided 2026-09-09). A daily ingest
   prices one departure date per horizon, so a horizon filter left each carrier with one
   dot per observed day and no curve; the chart draws every horizon together instead.
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

### Phone behaviour

Specified in S13. The design is drawn for a pointer: it wires `onMouseMove`/`onMouseLeave`
and nothing else, so on a phone the synthetic `mousemove` after a tap leaves the hairline
stuck wherever the finger landed with no `mouseleave` to clear it. These three behaviours
are the gap, and S15 implements them.

**Hover → tap-to-stick.** Use pointer events and branch on `pointerType`.

- `mouse`: unchanged — track on move, clear on leave.
- `touch` / `pen`: a tap inside the plot snaps the hairline to the nearest date index and
  it **stays**. A tap at another x moves it. A tap outside the plot, the drawer backdrop,
  or `Escape` clears it. No long-press and no drag-to-scrub: under 900px the plot is a
  760px-wide box inside `overflow-x:auto`, so a horizontal drag has to pan it. Leave
  `touch-action` alone for that reason.
- The hover card must clamp to the *scroll container's visible* box, not the 760px plot:
  at 250px wide on a 360px screen the design's `left:<pct>%` anchor puts it off-screen for
  most of the year. Clamp with 8px margins inside the visible rect, keeping the existing
  flip to the left of the hairline past 58 %.
- Keep the card `pointer-events:none`, so a tap that lands on it falls through to the plot
  and moves the hairline instead of being swallowed.
- Keyboard parity, per the same rule that makes the legend load-bearing: the plot is
  focusable, `←`/`→` move the stuck index by one date, `Home`/`End` jump to the ends,
  `Escape` clears — the tooltip is reachable without a pointer at all.

**Event drawer.** `min(430px, 92vw)` already fits a phone; what is missing is modal
behaviour.

- Opening locks `<body>` scroll (the drawer has its own `overflow-y:auto`), moves focus to
  the drawer heading, and traps Tab inside it. `Escape` and a backdrop tap close it, and
  focus returns to whatever opened it — the pin or the feed row.
- A stuck hairline survives the drawer, so closing returns the reader to the date they
  were reading.
- The Close button's `4px 10px` padding is ~24px tall; pad its hit area to 44px without
  growing the glyph, same rule as the pins.
- Drop `backdrop-filter: blur(2px)` under 620px and keep the `rgba(10,11,18,0.6)` scrim —
  a full-screen blur is the most expensive thing on the page for the least effect on a
  small screen.
- No swipe-to-dismiss: a horizontal swipe already belongs to the plot's scroll container.

**Filter bar overflow.** The design sets `flex-wrap:wrap` *and* `overflow-x:auto` on the
same element. A wrapping flex container never overflows horizontally, so the
`overflow-x:auto` is dead code that makes it look like the case is handled; at 360px the
four groups stack into four rows and push the chart below the fold.

- Drop `overflow-x:auto` from the bar. The bar wraps; the **carrier chip group alone**
  scrolls (`flex-wrap:nowrap; overflow-x:auto; min-width:0`). It is the only group whose
  width grows with the data — the two segmented controls are 4 and 6 short fixed items and
  fit one 360px row.
- Fade the chip strip's right edge (16px mask) while chips remain off-screen, so the
  scroll is visible rather than guessed at. The chips are real `<button>`s, so focus
  scrolls them into view and the strip is keyboard-reachable for free.
- Under 620px the Origin group leaves the bar: it is display-only and the header's route
  tag already says `CDG → PK-NORTH`.
- Chips are `6px 11px` ≈ 28px tall; pad the hit area to 44px, not the chip.

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
