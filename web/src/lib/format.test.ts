import { describe, expect, it } from "vitest";

import { live, playing } from "./format";

describe("a run that keeps another from starting", () => {
  it("is one playing, not one whose write-up goes on", () => {
    const analyzing = { status: "failed" as const, analyzing: true };
    expect(live(analyzing)).toBe(true); // still followed until its write-up lands
    expect(playing(analyzing)).toBe(false); // and the fix can run again meanwhile
    expect(playing({ status: "running" })).toBe(true);
    expect(playing({ status: "done" })).toBe(false);
  });
});
