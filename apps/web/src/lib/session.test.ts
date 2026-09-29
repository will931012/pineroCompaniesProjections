import { describe, expect, it } from "vitest";
import { hasRole, safeNextPath } from "./session";

describe("safeNextPath", () => {
  it.each([
    ["/companies/AAPL", "/companies/AAPL"],
    ["/companies?q=app", "/companies?q=app"],
    [null, "/"],
    ["https://evil.example", "/"],
    ["//evil.example", "/"],
    ["/\\evil.example", "/"],
    ["javascript:alert(1)", "/"],
  ])("maps %s to %s", (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });
});

describe("hasRole", () => {
  it("follows the viewer < analyst < admin hierarchy", () => {
    expect(hasRole("admin", "analyst")).toBe(true);
    expect(hasRole("analyst", "analyst")).toBe(true);
    expect(hasRole("viewer", "analyst")).toBe(false);
    expect(hasRole(undefined, "viewer")).toBe(false);
  });
});
