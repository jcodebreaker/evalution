import { CircleAlert, Minus, Plus } from "lucide-react";
import { type ReactNode, useState } from "react";
import { ApiError, api, errorText } from "../../lib/api";
import type { TestSetup, TestSetupChange } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Input } from "../../ui/field";
import { Seg, Switch } from "../connect/kit";
import { initials } from "./words";

/** How many copies of the app may run side by side. */
export const MOST_COPIES = 3;

const LANGUAGES = [
  "English",
  "French",
  "Spanish",
  "German",
  "Portuguese",
  "Italian",
  "Dutch",
  "Arabic",
  "Japanese",
  "Chinese",
];

function timeZones(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return ["UTC", "Europe/Lisbon", "Europe/London", "America/New_York", "Asia/Tokyo"];
  }
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2" aria-label={title}>
      <h3 className="m-0 text-sm font-medium text-ink-3">{title}</h3>
      {children}
    </section>
  );
}

/**
 * What a person can change about how tests run: the test person, what the copy starts with, when
 * it's put back, and how many copies run side by side. A change is checked again before the next
 * run, and the panel says so.
 */
export function SetupPanel({
  target,
  setup,
  locked,
  onChanged,
}: {
  target: string;
  setup: TestSetup;
  /** The copy is being set up now: nothing changes until it's done. */
  locked: boolean;
  onChanged: (setup: TestSetup) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(setup.person);
  const [file, setFile] = useState(setup.start.file ?? "");
  const [picking, setPicking] = useState(false);
  const fields = setup.supports.person;
  const has = (field: string) => fields.includes(field);
  const busy = saving || locked;

  const change = async (body: TestSetupChange) => {
    setSaving(true);
    setError(null);
    try {
      onChanged(await api.changeTestSetup(target, body));
      return true;
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.status === 409 && !failure.message
          ? "It's being set up now. Change this when it's done."
          : errorText(failure, "Couldn't save it. Try again."),
      );
      return false;
    } finally {
      setSaving(false);
    }
  };

  const savePerson = async () => {
    const person: TestSetupChange["person"] = {};
    for (const field of fields) {
      const key = field as keyof TestSetup["person"];
      if (key === "new_every_try") continue;
      if (key in draft && draft[key] !== setup.person[key]) {
        Object.assign(person, { [key]: draft[key] });
      }
    }
    if (!Object.keys(person).length || (await change({ person }))) setEditing(false);
  };

  // An app that keeps no name for a person names its own: the card says so, and makes none up.
  const named = has("name");
  const meta = [
    has("language") ? setup.person.language : null,
    has("timezone") ? setup.person.timezone : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const seeding = picking || setup.start.kind === "seed";

  return (
    <aside
      className="flex min-w-0 flex-col gap-[18px] rounded-2xl border border-rule bg-group p-5 shadow-[0_1px_2px_var(--shadow)] max-md:p-4"
      aria-label="Test setup"
    >
      <h2 className="m-0 text-[15px] font-semibold">Test setup</h2>

      <Section title="Test person">
        {fields.length ? (
          <>
            <div className="flex min-w-0 items-center gap-2.5">
              {named ? (
                <span className="grid size-[34px] shrink-0 place-items-center rounded-full bg-tint-soft text-sm font-semibold text-tint-ink">
                  {initials(setup.person.name || "Test person")}
                </span>
              ) : null}
              <span className="flex min-w-0 flex-1 flex-col">
                <b className="truncate font-medium">
                  {(named && setup.person.name) || "Test person"}
                </b>
                {meta ? <span className="truncate text-sm text-ink-3">{meta}</span> : null}
                {named ? null : (
                  <span className="truncate text-sm text-ink-3">
                    {target} names its own test person
                  </span>
                )}
              </span>
              {fields.some((field) => field !== "new_every_try") ? (
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={busy}
                  aria-expanded={editing}
                  onClick={() => {
                    setDraft(setup.person);
                    setEditing(!editing);
                  }}
                >
                  Edit
                </Button>
              ) : null}
            </div>
            {editing ? (
              <form
                className="flex flex-col gap-2 text-sm text-ink-2"
                onSubmit={(event) => {
                  event.preventDefault();
                  void savePerson();
                }}
              >
                {has("name") ? (
                  <label htmlFor="test-person-name" className="flex flex-col gap-1">
                    Name
                    <Input
                      id="test-person-name"
                      value={draft.name}
                      onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                    />
                  </label>
                ) : null}
                {has("language") ? (
                  <label htmlFor="test-person-language" className="flex flex-col gap-1">
                    Language
                    <Input
                      id="test-person-language"
                      list="test-person-languages"
                      value={draft.language ?? ""}
                      onChange={(event) =>
                        setDraft({ ...draft, language: event.target.value || null })
                      }
                    />
                    <datalist id="test-person-languages">
                      {LANGUAGES.map((language) => (
                        <option key={language} value={language} />
                      ))}
                    </datalist>
                  </label>
                ) : null}
                {has("timezone") ? (
                  <label htmlFor="test-person-zone" className="flex flex-col gap-1">
                    Time zone
                    <Input
                      id="test-person-zone"
                      list="test-person-zones"
                      value={draft.timezone ?? ""}
                      onChange={(event) =>
                        setDraft({ ...draft, timezone: event.target.value || null })
                      }
                    />
                    <datalist id="test-person-zones">
                      {timeZones().map((zone) => (
                        <option key={zone} value={zone} />
                      ))}
                    </datalist>
                  </label>
                ) : null}
                <div className="flex justify-end gap-2">
                  <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                  <Button type="submit" size="sm" disabled={busy}>
                    Save
                  </Button>
                </div>
              </form>
            ) : null}
            {has("new_every_try") ? (
              <div className="flex items-center gap-2.5">
                <span>New person every try</span>
                <span className="ms-auto">
                  <Switch
                    on={setup.person.new_every_try}
                    label="New person every try"
                    disabled={busy}
                    onChange={(on) => void change({ person: { new_every_try: on } })}
                  />
                </span>
              </div>
            ) : null}
          </>
        ) : (
          <p className="m-0 text-sm text-ink-2">This app picks its own test person</p>
        )}
      </Section>

      <Section title="Starts with">
        <Seg
          name="Starts with"
          value={seeding ? "seed" : "empty"}
          disabled={busy}
          options={[
            { value: "empty" as const, label: "Nothing", aside: "clean slate" },
            ...(setup.supports.seed
              ? [
                  {
                    value: "seed" as const,
                    label: "Sample data",
                    aside: setup.start.file ?? "a file",
                  },
                ]
              : []),
          ]}
          onPick={(value) => {
            if (value === "empty") {
              setPicking(false);
              void change({ start: { kind: "empty" } });
            } else if (setup.start.file) {
              void change({ start: { kind: "seed", file: setup.start.file } });
            } else {
              setPicking(true);
            }
          }}
        />
        {setup.supports.seed && seeding ? (
          <form
            className="flex gap-1.5"
            onSubmit={(event) => {
              event.preventDefault();
              if (file.trim())
                void change({ start: { kind: "seed", file: file.trim() } }).then((done) => {
                  if (done) setPicking(false);
                });
            }}
          >
            <Input
              aria-label="Sample data file"
              placeholder="seed.sql"
              value={file}
              onChange={(event) => setFile(event.target.value)}
              disabled={busy}
            />
            <Button
              type="submit"
              variant="secondary"
              disabled={busy || !file.trim() || file.trim() === setup.start.file}
            >
              Use
            </Button>
          </form>
        ) : null}
      </Section>

      <Section title="Reset">
        <Seg
          name="Reset"
          value={setup.reset}
          disabled={busy}
          options={[
            { value: "try" as const, label: "Before every try", aside: "cleanest" },
            { value: "test" as const, label: "Before every test", aside: "faster" },
          ]}
          onPick={(value) => void change({ reset: value })}
        />
      </Section>

      <div className="flex items-center gap-2.5">
        <span>Copies side by side</span>
        <span className="ms-auto inline-flex h-9 items-center rounded-[10px] border border-rule-strong bg-group">
          <Button
            variant="ghost"
            size="icon"
            aria-label="Fewer copies"
            disabled={busy || setup.copies <= 1}
            onClick={() => void change({ copies: setup.copies - 1 })}
          >
            <Minus />
          </Button>
          <b className="min-w-[26px] text-center font-semibold tabular-nums" data-testid="copies">
            {setup.copies}
          </b>
          <Button
            variant="ghost"
            size="icon"
            aria-label="More copies"
            disabled={busy || setup.copies >= MOST_COPIES}
            onClick={() => void change({ copies: setup.copies + 1 })}
          >
            <Plus />
          </Button>
        </span>
      </div>

      {setup.needs_check ? (
        <p className="m-0 flex items-center gap-2 rounded-[10px] bg-unjudged-soft px-3 py-2 text-sm text-unjudged">
          <CircleAlert className="size-4 shrink-0" aria-hidden />
          Changed. It's checked again before the next run.
        </p>
      ) : null}
      {error ? <p className="m-0 text-sm text-fail">{error}</p> : null}
    </aside>
  );
}
