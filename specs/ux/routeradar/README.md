# RouteRadar — the dashboard design

Source of truth for the dashboard's look and behaviour, pulled from the Claude Design
project "RouteRadar CDG Pakistan Dashboard"
(https://claude.ai/design/p/44fdfdf1-7cff-49b4-a993-a6249d709a39, file `RouteRadar.dc.html`)
on 2026-09-09. Edit it there and re-pull; do not hand-edit the copy here.

- `RouteRadar.dc.html` — the design: markup in `<x-dc>`, behaviour and fake data in the
  `data-dc-script` block. The fake data (CARRIERS, BANDS, EVENTS, the `fare()` model) is a
  readable statement of what the real data export must provide; see
  `../design-system.md` for the translation into the JSON contract.
- `support.js` — the `.dc.html` runtime (needs React on `window`; not needed to *read* the
  design, only to render it standalone).
- `_ds/nocturne-*/` — the Nocturne design system it is built on: `styles.css` (tokens and
  classes), `readme.md` (rules). `_ds_bundle.js` is an empty namespace stub.

The Nocturne component-preview pages (`components/*.html`, `foundations/*.html`) were
not pulled; `readme.md` describes them.
