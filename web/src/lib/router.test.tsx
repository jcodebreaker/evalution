// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { usePlace } from "./router";

afterEach(() => window.history.replaceState(null, "", "/"));

it("keeps the chosen try in the address, and drops it for another test or run", () => {
  window.history.replaceState(null, "", "/?target=shop&view=tests&run=r9&case=cart&try=2");
  const { result } = renderHook(() => usePlace());
  // A link, a reload or Back opens at the try it names.
  expect(result.current[0].try).toBe("2");
  act(() => result.current[1]({ try: "3" }, true));
  expect(new URLSearchParams(window.location.search).get("try")).toBe("3");
  act(() => result.current[1]({ run: "r10" }));
  expect(result.current[0].try).toBeNull();
  expect(window.location.search).not.toContain("try=");
  act(() => result.current[1]({ try: "1" }, true));
  act(() => result.current[1]({ case: "login" }));
  expect(result.current[0].try).toBeNull();
});
