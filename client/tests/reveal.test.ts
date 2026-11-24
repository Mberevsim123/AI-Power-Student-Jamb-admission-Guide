import { describe, expect, it } from "vitest";
import { snap } from "../src/lib/reveal";

describe("reveal boundaries", () => {
  it("stops at the end of a word", () => {
    const text = "Upload your results today";
    expect(text.slice(0, snap(text, 9))).toBe("Upload your");
  });
  it("reveals a table row whole", () => {
    const text = "Intro\n| A | B |\n|---|---|\n| 1 | 2 |\nAfter";
    expect(text.slice(0, snap(text, 8))).toBe("Intro\n| A | B |\n");
  });
  it("never shows half a Markdown link", () => {
    const text = "Use [JAMB e-Facility](https://efacility.jamb.gov.ng) now";
    expect(text.slice(0, snap(text, 8))).toBe(
      "Use [JAMB e-Facility](https://efacility.jamb.gov.ng)",
    );
  });
  it("never runs past the end", () => {
    expect(snap("short", 99)).toBe(5);
  });
});
