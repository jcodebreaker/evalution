import { expect, it } from "vitest";
import { TargetDetailSchema } from "../../lib/schema";
import { bodyOf, formOf } from "./target-form";

/** A saved target with settings set in the config file only, which the Details form doesn't
 * show: a save from the form sends each back as it read it. */
const saved = TargetDetailSchema.parse({
  name: "shop",
  adapter: "http",
  options: {
    send: { method: "POST", url: "http://shop.test/chat", body: { m: "{message}" } },
    reply: "$.reply",
    done: {
      poll: { url: "http://shop.test/s", until: "$.done", interval: 2, failed_when: "$.err" },
    },
  },
  phoenix: {
    url: "http://phoenix.test:6006",
    project: "shop",
    public_url: "https://phoenix.example.test",
    mirror: false,
  },
  code: { path: "app", ignore: [], area_parallel: 4, summarize: 0.5, area_model: "light" },
});

it("sends back mirroring, Phoenix's public address, the learn's parallel areas and the poll", () => {
  const body = bodyOf(formOf(saved), saved.options);
  if (typeof body === "string") throw new Error(body);
  expect(body.phoenix).toEqual({
    url: "http://phoenix.test:6006",
    project: "shop",
    public_url: "https://phoenix.example.test",
    mirror: false,
  });
  expect(body.code).toMatchObject({ area_parallel: 4, summarize: 0.5, area_model: "light" });
  expect(body.options.done).toEqual({
    poll: { url: "http://shop.test/s", until: "$.done", interval: 2, failed_when: "$.err" },
  });
});

it("drops the public address once the form points at another Phoenix, and keeps the defaults", () => {
  const form = { ...formOf(saved), phoenixUrl: "http://other.test:6006" };
  const body = bodyOf(form, saved.options);
  if (typeof body === "string") throw new Error(body);
  expect(body.phoenix).toEqual({ url: "http://other.test:6006", project: "shop", mirror: false });
  const plain = TargetDetailSchema.parse({
    name: "shop",
    adapter: "http",
    options: saved.options,
    phoenix: { url: "http://phoenix.test:6006", project: "shop" },
  });
  const again = bodyOf(formOf(plain), plain.options);
  if (typeof again === "string") throw new Error(again);
  expect(again.phoenix).toEqual({ url: "http://phoenix.test:6006", project: "shop" });
});
