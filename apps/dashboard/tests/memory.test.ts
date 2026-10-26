import { describe, expect, it } from "vitest";

import { headline, scorePercent } from "@/lib/api/memory";

describe("memory helpers", () => {
  it("uses the template's first line as the headline", () => {
    expect(headline("payment. network. silence.\nOnset: x.")).toBe("payment. network. silence.");
    expect(headline("single line")).toBe("single line");
  });

  it("clamps the score bar", () => {
    expect(scorePercent(0.634)).toBe(63);
    expect(scorePercent(1.4)).toBe(100);
    expect(scorePercent(-1)).toBe(0);
  });
});
