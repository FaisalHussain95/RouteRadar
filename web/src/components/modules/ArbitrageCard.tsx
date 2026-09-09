import { formatDay, parseDay } from "../../lib/dates";
import { euros, signedEuros } from "../../lib/format";
import type { Arbitrage } from "../../types";

const VERDICT: Record<Arbitrage["verdict"], string> = {
  lhe: "Fly LHE + M-11 transfer",
  skt: "Fly SKT direct",
  either: "Either — the spread is inside the transfer",
};

/** `spread_eur` is `lhe_eur - skt_eur`, so a positive spread means Sialkot is cheaper. The
 * design captions the card "spread SKT − LHE", which is the other sign; one convention
 * across the codebase beats matching a caption, so the caption is reworded here rather than
 * the data flipped. */
export function ArbitrageCard({ arbitrage }: { arbitrage: Arbitrage | null }) {
  return (
    <section className="card module">
      <div className="kicker">Airport arbitrage</div>
      <h3>LHE + M-11 transfer vs. SKT direct</h3>
      {arbitrage === null ? (
        <p className="empty-line" data-testid="arbitrage-empty">
          No date priced into both Lahore and Sialkot yet — a spread needs two fares.
        </p>
      ) : (
        <ArbitrageBody arbitrage={arbitrage} />
      )}
    </section>
  );
}

function ArbitrageBody({ arbitrage }: { arbitrage: Arbitrage }) {
  const worst = Math.max(arbitrage.lhe_eur, arbitrage.skt_eur, 1);
  // Positive when Lahore is the cheaper ticket; the transfer is what it has to beat.
  const lheAdvantage = -arbitrage.spread_eur;
  const net = lheAdvantage - arbitrage.ground_transfer_eur;
  return (
    <>
      <div className="arb-grid">
        <span style={{ color: "rgba(233,233,237,0.6)" }}>LHE</span>
        <span className="arb-track">
          <span
            className="arb-bar lhe"
            style={{ width: `${Math.round((arbitrage.lhe_eur / worst) * 100)}%` }}
          />
        </span>
        <span>{euros(arbitrage.lhe_eur)}</span>
        <span style={{ color: "rgba(233,233,237,0.6)" }}>SKT</span>
        <span className="arb-track">
          <span
            className="arb-bar skt"
            style={{ width: `${Math.round((arbitrage.skt_eur / worst) * 100)}%` }}
          />
        </span>
        <span>{euros(arbitrage.skt_eur)}</span>
      </div>
      <div className="arb-foot">
        <div className="arb-spread">{signedEuros(arbitrage.spread_eur)}</div>
        <div style={{ fontSize: 12, color: "var(--text-muted-55)", marginTop: 2 }}>
          spread LHE − SKT on {formatDay(parseDay(arbitrage.departure_date))}
        </div>
        <div className="verdict-pill">{VERDICT[arbitrage.verdict]}</div>
        <div
          className="mono"
          style={{ fontSize: 11.5, color: "rgba(233,233,237,0.5)", marginTop: 7 }}
        >
          {net >= 0 ? "saves" : "costs"} {euros(Math.abs(net))} after{" "}
          {euros(arbitrage.ground_transfer_eur)} ground transfer · {arbitrage.ground_time} road,
          M-11
        </div>
      </div>
    </>
  );
}
