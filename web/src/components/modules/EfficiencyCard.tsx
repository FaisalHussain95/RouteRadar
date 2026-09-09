import { domainOf } from "../../lib/chart";
import { carrierColor, euros, hours } from "../../lib/format";
import type { Carrier, Efficiency } from "../../types";

export function EfficiencyCard({
  efficiency,
  carriers,
  emptyLine,
}: {
  efficiency: Efficiency[];
  carriers: Carrier[];
  emptyLine: string;
}) {
  const byCode = new Map(carriers.map((c) => [c.code, c]));
  const dots = efficiency.flatMap((e) => {
    const carrier = byCode.get(e.carrier);
    return carrier === undefined ? [] : [{ ...e, carrier }];
  });
  const [priceLo, priceHi] = domainOf(
    dots.map((d) => d.price_eur),
    40,
  );
  const [transitLo, transitHi] = domainOf(
    dots.map((d) => hours(d.transit_minutes)),
    0.5,
  );

  return (
    <section className="card module">
      <div className="kicker">Carrier efficiency matrix</div>
      <h3>Price vs. total transit time</h3>
      {dots.length === 0 ? (
        <p className="empty-line" data-testid="efficiency-empty">
          {emptyLine}
        </p>
      ) : (
        <>
          <div className="scatter">
            <div className="wash" />
            <div className="quadrant" />
            <div className="sweet">Sweet spot</div>
            {dots.map((d) => {
              const x = ((hours(d.transit_minutes) - transitLo) / (transitHi - transitLo)) * 100;
              const y = (1 - (d.price_eur - priceLo) / (priceHi - priceLo)) * 100;
              const color = carrierColor(d.carrier);
              return (
                <div key={d.carrier.code}>
                  <div
                    className="dot"
                    data-testid={`efficiency-dot-${d.carrier.code}`}
                    style={{
                      left: `${x}%`,
                      top: `${y}%`,
                      background: color,
                      boxShadow: `0 0 12px ${color}`,
                    }}
                  />
                  <div
                    className="dot-label"
                    style={{
                      top: `${Math.min(96, Math.max(4, y))}%`,
                      color,
                      ...(x > 55
                        ? { right: `${100 - x}%`, marginRight: 7, textAlign: "right" as const }
                        : { left: `${x}%`, marginLeft: 7 }),
                    }}
                  >
                    {d.carrier.name} {euros(d.price_eur)}
                  </div>
                </div>
              );
            })}
            <div className="scatter-y" style={{ top: -2 }}>
              {euros(priceHi)}
            </div>
            <div className="scatter-y" style={{ bottom: -2 }}>
              {euros(priceLo)}
            </div>
          </div>
          <div className="scatter-x">
            <span>{transitLo.toFixed(1)}h</span>
            <span>total transit</span>
            <span>{transitHi.toFixed(1)}h</span>
          </div>
        </>
      )}
    </section>
  );
}
