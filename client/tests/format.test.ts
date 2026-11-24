import { describe, expect, it } from "vitest";
import { displayName } from "../src/lib/format";

describe("university display names", () => {
  it("drops a trailing state that is shown separately", () => {
    expect(displayName("Abdulfattah Durojaiye University, Abeokuta, Ogun State", "Ogun")).toBe(
      "Abdulfattah Durojaiye University, Abeokuta",
    );
    expect(displayName("Federal University Gashua, Yobe", "Yobe")).toBe(
      "Federal University Gashua",
    );
  });
  it("keeps names where the state is part of the institution's name", () => {
    expect(displayName("Rivers State University", "Rivers")).toBe("Rivers State University");
    expect(displayName("Lagos State University, Ojo", "Lagos")).toBe(
      "Lagos State University, Ojo",
    );
  });
});
