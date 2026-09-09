import { describe, expect, it } from "vitest";

import { hasOverflow } from "./dom";

describe("hasOverflow", () => {
  it("is false when everything fits", () => {
    expect(hasOverflow({ scrollWidth: 300, clientWidth: 300, scrollLeft: 0 })).toBe(false);
  });

  it("is true while chips remain off the right edge", () => {
    expect(hasOverflow({ scrollWidth: 600, clientWidth: 300, scrollLeft: 0 })).toBe(true);
    expect(hasOverflow({ scrollWidth: 600, clientWidth: 300, scrollLeft: 120 })).toBe(true);
  });

  it("goes out once the strip is scrolled to its end", () => {
    expect(hasOverflow({ scrollWidth: 600, clientWidth: 300, scrollLeft: 300 })).toBe(false);
  });
});
