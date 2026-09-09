import { euros } from "../../lib/format";
import type { SeasonalGauge } from "../../types";

/** The `+NN%` gauge. `method` is printed rather than written here: the export decides what
 * the comparison actually is (the wedding premium against the Feb/Mar baseline), and the
 * card should not restate it from memory. */
export function SeasonalGaugeCard({
  gauge,
  emptyLine,
}: {
  gauge: SeasonalGauge | null;
  emptyLine: string;
}) {
  return (
    <section className="card module">
      <div className="kicker">Seasonal multiplier</div>
      <h3>Current window vs. February baseline</h3>
      {gauge === null ? (
        <p className="empty-line" data-testid="gauge-empty">
          {emptyLine}
        </p>
      ) : (
        <>
          <div className="gauge-pct">
            {gauge.pct >= 0 ? "+" : "−"}
            {Math.abs(gauge.pct)}%
          </div>
          <div className="gauge-track">
            <div
              className="gauge-fill"
              style={{ width: `${Math.min(100, Math.abs(gauge.pct) * 1.3)}%` }}
            />
          </div>
          <div className="gauge-row">
            <span>
              {euros(gauge.current_avg_eur)} <span className="unit">current avg</span>
            </span>
            <span style={{ color: "rgba(233,233,237,0.6)" }}>
              {euros(gauge.baseline_avg_eur)} <span className="unit">Feb shoulder</span>
            </span>
          </div>
          <p className="gauge-method">{gauge.method}</p>
        </>
      )}
    </section>
  );
}
