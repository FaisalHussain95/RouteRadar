Flight Price Intelligence Engine: Paris to Pakistan (CDG → ISB/LHE/SKT)
Executive Summary
This architecture defines a specialized analytics tracker designed to analyze, project, and explain airfare dynamics from Paris (CDG/ORY) to northern Pakistan (Islamabad [ISB], Lahore [LHE], and Sialkot [SKT]). By enforcing strict operational constraints—maximum 1 stop, under 7 hours layover—the system eliminates impractical multi-ticket transfers to isolate premium-to-mid-tier full-service carriers.
The core differentiator is an integrated dual-layer context engine: predictable, cyclical price drivers (such as the winter Desi wedding peak, Ramadan, Eid, Hajj, and French vacation windows) are handled deterministically, while unexpected price shocks (airspace closures, regulatory rulings, carrier capacity shifts) are captured through automated, filtered news ingestion. The result is a unified time-series dataset that explains not just how much a flight costs, but why the price moved.
1. Route Constraints & Flight Scope
 * Origin: Paris Charles de Gaulle (CDG), Paris Orly (ORY)
 * Destinations: Islamabad (ISB), Lahore (LHE), Sialkot (SKT)
 * Maximum Stops: 1 Stop max (plus direct flights where available)
 * Layover Threshold: Connection duration strictly \le 7 hours (total route time capped at \approx 14–15 hours)
 * Core Carrier Matrix:
   * PIA: Direct non-stop service (ISB/LHE); low frequency, steep bucket fill rate.
   * Gulf Air: Bahrain (BAH) hub; historically the aggressive price setter on 1-stop routes.
   * Qatar Airways: Doha (DOH) hub; consistent high-frequency coverage to all three airports; premium baseline.
   * Emirates / flydubai: Dubai (DXB) hub; codeshare connectivity into SKT; high seat volume.
   * Turkish Airlines: Istanbul (IST) hub; direct competition on ISB and LHE via northern routing.
   * Saudia: Jeddah (JED) / Riyadh (RUH) hubs; aggressive baseline fares, severe capacity competition during religious windows.
2. Event & Cultural Context Engine (Deterministic Layer)
Rather than using natural-language news scrapers to detect predictable holidays, an algorithmic calendar engine calculates and assigns tags to each queried departure date.
+-----------------------------------------------------------------------------------+
|                           DETERMINISTIC EVENT ENGINE                              |
+--------------------------+--------------------------------+-----------------------+
| Window Type              | Date Range / Logic             | Expected Impact       |
+--------------------------+--------------------------------+-----------------------+
| Winter Wedding Rush      | Nov 15 – Jan 15 (Sustained)    | Baseline lift (1.4-1.8x)
| Christmas / New Year     | Dec 18 – Jan 05                | Peak winter spike     |
| French Summer Holidays   | Jul 01 – Aug 31 (Zones C)      | Heavy European exit tax|
| Ramadan Phase 1          | Days 1–15 of Ramadan           | Outbound demand drop  |
| Chaand Raat / Eid-ul-Fitr| Hijri Dynamic (Eid -4 to +2 d) | Highest sudden spike  |
| Hajj & Eid-ul-Adha       | Dhul Hijjah Corridor           | Gulf seat rationing   |
+--------------------------+--------------------------------+-----------------------+

 * The Wedding Season Multiplier: Spanning mid-November through mid-January, the South Asian wedding corridor creates an elevated demand floor that intersects directly with the Western Christmas travel surge.
 * The Hijri Shift: Uses astronomical or calculated Hijri data (hijri-converter) to account for the ~10–11 day annual Gregorian drift for Ramadan, Eid-ul-Fitr, Eid-ul-Adha, and the Hajj pilgrimage.
 * The Transit Hub Effect: Accounts for indirect bottlenecks where flights into Pakistan become expensive because pilgrims fill seats from Europe into Gulf hubs (JED, RUH, DOH, BAH) during Umrah and Hajj.
3. Exogenous Shock & News Engine (Dynamic Layer)
To backtest anomalies and isolate irregular pricing movements, a focused news pipeline flags external operational events.
 * Targeted News Sources:
   * GDELT Project: Free, open, multi-year historical dataset categorizing geopolitical and transport disruptions by standard taxonomy.
   * Event Registry (NewsAPI.ai): Topic-clustered event tracking to prevent duplicate coverage of single incidents.
   * Aviation Aggregators: Specialized feeds (Aviation Herald, FlightGlobal, regional civil aviation authorities) for traffic rights and airspace updates.
 * Query Taxonomy Filters (Anti-Noise):
   * Regulatory: "EASA" AND "PIA", "bilateral air service", "traffic rights Pakistan", "civil aviation authority".
   * Airspace & Disruption: "Middle East airspace", "Gulf Air route disruption", "CDG strike", "Doha transit delay".
   * Pilgrimage / Visas: "Umrah quota", "Hajj flight schedule", "Saudi transit visa".
4. End-to-End System Architecture
                 [ Automated Ingestion Pipeline ]
                                │
       ┌────────────────────────┴────────────────────────┐
       ▼                                                 ▼
[ Flight Scraping Agent ]                     [ News & Event Ingest ]
- SerpApi / Apify Google Flights              - GDELT Historical Engine
- Params: CDG -> ISB,LHE,SKT                  - Taxonomical Keywords:
  max_stops=1, max_dur=15h                      Airspace, EASA, Strikes
- Rolling departure windows:                  - Algorithmic Calendar:
  14, 30, 60, 90, 120, 180 days                 Hijri, Wedding, French Vac
       │                                                 │
       └────────────────────────┬────────────────────────┘
                                ▼
               [ Unified Timeseries Database ]
               - DuckDB / PostgreSQL / Supabase
               - Joined by Departure Date & Booking Date
                                │
                                ▼
               [ Analytical / BI Dashboard ]
               - Looker Studio / Grafana / Metabase
               - Lowest Fare Curves by Carrier
               - Cultural Window Background Shading
               - High-Severity Event Markers

5. Core Analytical Metrics & Research Questions
 * Airport Price Arbitrage: Quantifying the exact spread between flying into Lahore (LHE) versus Sialkot (SKT) to determine the break-even threshold for ground transport (M-11 motorway).
 * Carrier Efficiency Index: Mapping flight price against total transit duration to establish the value leader (e.g., assessing Gulf Air's average discount vs. Qatar Airways relative to connection length).
 * Wedding Window Premium: Calculating the price multiplier between baseline off-peak travel (e.g., February/March) and peak wedding season departures.
 * Lead-Time Curve: Establishing the optimal booking horizon (days out) across seasonal bands to identify when dynamic pricing escalates across Gulf carriers.
