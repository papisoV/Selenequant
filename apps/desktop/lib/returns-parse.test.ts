import { describe, it, expect, vi } from "vitest";
import { parseReturnsSeries, genSampleReturns } from "./returns-parse";

describe("parseReturnsSeries", () => {
  it("parses one number per line into returns + daily index", () => {
    const parsed = parseReturnsSeries("0.001\n0.002\n-0.003\n");
    expect(parsed).not.toBeNull();
    expect(parsed!.columns.ret).toEqual([0.001, 0.002, -0.003]);
    expect(parsed!.index).toHaveLength(3);
    expect(parsed!.index[0]).toBe("2024-01-01");
    expect(parsed!.index[1]).toBe("2024-01-02");
  });

  it("returns null for empty / whitespace-only input", () => {
    expect(parseReturnsSeries("")).toBeNull();
    expect(parseReturnsSeries("   \n\t ")).toBeNull();
  });

  it("returns null and reports the offending line for invalid input", () => {
    const onInvalid = vi.fn();
    const parsed = parseReturnsSeries("0.001\nabc\n0.003", onInvalid);
    expect(parsed).toBeNull();
    expect(onInvalid).toHaveBeenCalledWith(2, "abc");
  });

  it("accepts numbers in scientific / decimal formats", () => {
    const parsed = parseReturnsSeries("1e-3\n.5\n-0.25");
    expect(parsed!.columns.ret).toEqual([0.001, 0.5, -0.25]);
  });
});

describe("genSampleReturns", () => {
  it("produces n entries with deterministic values", () => {
    const a = genSampleReturns(5);
    const b = genSampleReturns(5);
    expect(a.index).toHaveLength(5);
    expect(a.columns.ret).toHaveLength(5);
    expect(a.columns.ret).toEqual(b.columns.ret);
    expect(a.index[0]).toBe("2024-01-01");
  });
});
