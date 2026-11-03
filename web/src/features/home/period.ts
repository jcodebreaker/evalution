import type { Period } from "../../lib/schema";

export const PERIODS: [Period, string][] = [
  ["7d", "This week"],
  ["30d", "This month"],
  ["all", "All time"],
];

/** The period in a sentence ("this week"). */
export const NOW: Record<Period, string> = {
  "7d": "this week",
  "30d": "this month",
  all: "",
};
