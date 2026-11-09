import { Activity, CircleAlert, Minus, Plus, UserRound } from "lucide-react";
import { type ReactNode, useState } from "react";
import { ApiError, api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { TestSetup, TestSetupChange } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Input } from "../../ui/field";
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

const SECTION = "mb-2 text-[11px] font-semibold tracking-[0.06em] text-ink-3 uppercase";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-4 flex flex-col" aria-label={title}>
      <h3 className={cn(SECTION, "m-0 mb-2")}>{title}</h3>
      {children}
    </section>
  );
}

/** One choice of a few, as a card row with a round mark. */
function Choice({
  name,
  checked,
  disabled,
  label,
  aside,
  onPick,
}: {
  name: string;
  checked: boolean;
  disabled: boolean;
  label: string;
  aside: string;
  onPick: () => void;
}) {
  return (
    <label
      className={cn(
        "flex items-center gap-2.5 rounded-[10px] border px-2.5 py-2 has-focus-visible:outline-2 has-focus-visible:outline-ring",
        checked ? "border-tint bg-selection-strong" : "border-border",
        disabled && "opacity-60",
      )}
    >
      <input
        type="radio"
        name={name}
        className="sr-only"
        checked={checked}
        disabled={disabled}
        onChange={onPick}
      />
      <i
        className={cn(
          "size-3.5 shrink-0 rounded-full",
          checked ? "border-4 border-tint" : "border-[1.5px] border-input",
        )}
        aria-hidden
      />
      {label}
      <span className="ms-auto truncate text-[12.5px] text-ink-3">{aside}</span>
    </label>
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
      className="flex flex-col rounded-[14px] border border-border bg-group p-4"
      aria-label="Test setup"
    >
      <h2 className="m-0 flex items-baseline justify-between gap-2 text-[14px] font-semibold">
        Test setup
        <span className="text-[12.5px] font-normal text-ink-3">you can change this</span>
      </h2>

      <Section title="Test person">
        {fields.length ? (
          <>
            <div className="flex items-center gap-2.5 rounded-[11px] border border-border p-2.5">
              <span className="grid size-[38px] shrink-0 place-items-center rounded-full bg-up font-bold text-success">
                {named ? (
                  initials(setup.person.name || "Test person")
                ) : (
                  <UserRound className="size-4" aria-hidden />
                )}
              </span>
              <span className="flex min-w-0 flex-col">
                <b className="truncate font-semibold">
                  {(named && setup.person.name) || "Test person"}
                </b>
                {meta ? <span className="truncate text-[12.5px] text-ink-3">{meta}</span> : null}
                {named ? null : (
                  <span className="truncate text-[12.5px] text-ink-3">
                    {target} names its own test person
                  </span>
                )}
              </span>
              <Button
                variant="secondary"
                size="sm"
                className="ms-auto"
                disabled={busy}
                aria-expanded={editing}
                onClick={() => {
                  setDraft(setup.person);
                  setEditing(!editing);
                }}
              >
                Edit
              </Button>
            </div>
            {editing ? (
              <form
                className="mt-2 flex flex-col gap-2 text-[12.5px] text-ink-2"
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
                {has("new_every_try") ? (
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={draft.new_every_try}
                      onChange={(event) =>
                        setDraft({ ...draft, new_every_try: event.target.checked })
                      }
                    />
                    New person every try
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
          </>
        ) : (
          <p className="m-0 text-[13px] text-ink-2">This app picks its own test person</p>
        )}
      </Section>

      <Section title="Starts with">
        <div className="flex flex-col gap-1.5">
          <Choice
            name="start"
            checked={!seeding}
            disabled={busy}
            label="Nothing"
            aside="clean slate"
            onPick={() => {
              setPicking(false);
              void change({ start: { kind: "empty" } });
            }}
          />
          {setup.supports.seed ? (
            <Choice
              name="start"
              checked={seeding}
              disabled={busy}
              label="Sample data"
              aside={setup.start.file ?? "a file"}
              onPick={() => {
                if (setup.start.file)
                  void change({ start: { kind: "seed", file: setup.start.file } });
                else setPicking(true);
              }}
            />
          ) : null}
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
        </div>
      </Section>

      <Section title="Reset">
        <div className="flex flex-col gap-1.5">
          <Choice
            name="reset"
            checked={setup.reset === "try"}
            disabled={busy}
            label="Before every try"
            aside="cleanest"
            onPick={() => void change({ reset: "try" })}
          />
          <Choice
            name="reset"
            checked={setup.reset === "test"}
            disabled={busy}
            label="Before every test"
            aside="faster"
            onPick={() => void change({ reset: "test" })}
          />
        </div>
      </Section>

      <Section title="Copies side by side">
        <div className="flex items-center gap-2.5">
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="Fewer copies"
            disabled={busy || setup.copies <= 1}
            onClick={() => void change({ copies: setup.copies - 1 })}
          >
            <Minus />
          </Button>
          <b className="min-w-[18px] text-center tabular-nums" data-testid="copies">
            {setup.copies}
          </b>
          <Button
            variant="outline"
            size="icon-sm"
            aria-label="More copies"
            disabled={busy || setup.copies >= MOST_COPIES}
            onClick={() => void change({ copies: setup.copies + 1 })}
          >
            <Plus />
          </Button>
          <span className="ms-auto flex gap-1" aria-hidden>
            {Array.from({ length: Math.max(1, setup.copies) }, (_, index) => (
              <i
                key={index}
                className="block h-[18px] w-3.5 rounded-[4px] border border-tint bg-selection-strong"
              />
            ))}
          </span>
        </div>
      </Section>

      <Section title="Left alone">
        <div className="flex flex-wrap gap-1.5">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-bubble px-2 py-1 text-[12.5px] text-ink-2">
            <Activity className="size-3.5" aria-hidden />
            Traces · kept as evidence
          </span>
        </div>
      </Section>

      {setup.needs_check ? (
        <p className="m-0 mt-3.5 flex items-center gap-2 rounded-[10px] bg-warning/10 px-2.5 py-2 text-[12.5px] text-warning">
          <CircleAlert className="size-4 shrink-0" aria-hidden />
          Changed. It's checked again before the next run.
        </p>
      ) : null}
      {error ? <p className="m-0 mt-2.5 text-[13px] text-destructive">{error}</p> : null}
    </aside>
  );
}
