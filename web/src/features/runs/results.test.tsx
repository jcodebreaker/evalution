// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { CaseRowSchema } from "../../lib/schema";
import { Results, rowLine } from "./Results";

afterEach(cleanup);

const row = (fields: Record<string, unknown>) =>
  CaseRowSchema.parse({ case: "c", name: "A test", verdict: "pass", tries: ["passed"], ...fields });

it("says which layer broke and whose it is, not the log line, on a page test", () => {
  // Run 20261108-162620-839b's row: the page's health reason, in the server's plain words now.
  const refused = row({
    verdict: "fail",
    tries: ["failed"],
    label: "Failed check",
    plays: "page",
    reasons: ["the app answered a request from the page with an error, 503"],
    layers: { stored: "absent", answered: "unjudged", shown: "fail" },
  });
  expect(rowLine(refused)).toBe(
    "Answered ? · Shown ✕ · the app answered a request from the page with an error, 503",
  );
  const passed = row({
    plays: "page",
    layers: { stored: "pass", answered: "pass", shown: "pass" },
  });
  expect(rowLine(passed)).toBe("Stored ✓ · Answered ✓ · Shown ✓");
});

it("reads as before on a test played through the API, or from a server that doesn't say", () => {
  const failed = row({
    verdict: "fail",
    tries: ["failed"],
    label: "Failed check",
    reasons: ["the reply lacks 'Biscuit'"],
    about: "Remembers the dog",
    layers: { stored: "fail", answered: "pass", shown: "absent" },
  });
  expect(rowLine(failed)).toBe("Failed check · the reply lacks 'Biscuit'");
  expect(rowLine(row({ about: "Remembers the dog" }))).toBe("Remembers the dog");
});

it("marks each played test Page or API where the run played both ways", () => {
  const rows = [
    row({ case: "a", name: "A table shows", plays: "page" }),
    row({ case: "b", name: "Remembers the dog", plays: "api" }),
    row({ case: "n", name: "Not played", verdict: null, tries: [] }),
  ];
  render(<Results rows={rows} going={false} onTest={() => null} onRun={() => null} />);
  expect(screen.getAllByTestId("plays-chip").map((chip) => chip.textContent)).toEqual([
    "Page",
    "API",
  ]);
  cleanup();
  render(<Results rows={rows.slice(1, 2)} going={false} onTest={() => null} onRun={() => null} />);
  expect(screen.queryByTestId("plays-chip")).toBeNull();
  cleanup();
  // Every played test through the page: the run's header says it, once.
  render(<Results rows={rows.slice(0, 1)} going={false} onTest={() => null} onRun={() => null} />);
  expect(screen.queryByTestId("plays-chip")).toBeNull();
});
