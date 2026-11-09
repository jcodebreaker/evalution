import { useState } from "react";
import type { Scope } from "../../lib/api";
import { count } from "../../lib/format";
import type { CaseRow, PassMarks } from "../../lib/schema";
import { MoreMenu } from "../../ui/menu";
import { RunAll } from "../runs/RunAll";
import { Group } from "../shared/group";
import { Page, Toolbar, ToolbarTitle } from "../shared/layout";
import { Composer } from "./Composer";
import { PassMarksDialog } from "./PassMarksDialog";
import { TestRow } from "./TestRow";

/** How the tests play, in a sentence over the list: every test of an app plays one way. */
export function playsLine(target: string, rows: readonly CaseRow[]): string {
  const page = rows.filter((row) => row.plays === "page").length;
  if (page === rows.length) {
    return `Played through ${target}'s page: each test types each message there, and checks it shows.`;
  }
  if (page === 0)
    return `Played through ${target}'s API: each test sends its messages straight to it.`;
  return `Page tests type into ${target}'s page; API tests send straight to its API.`;
}

/**
 * The tests: what to test next (handed to the test writer), Run all, then every test with its
 * latest verdict. Pass marks sit in the header's menu.
 */
export function TestsView({
  scope,
  rows,
  passMarks,
  going,
  onAsk,
  onPassMarks,
  onGoing,
  onRunAll,
  onRun,
  onOpen,
}: {
  scope: Scope;
  rows: CaseRow[];
  passMarks: PassMarks;
  going: boolean;
  onAsk: (text: string) => Promise<void>;
  onPassMarks: () => Promise<unknown>;
  onGoing: () => void;
  onRunAll: () => void;
  onRun: (row: CaseRow) => void;
  onOpen: (row: CaseRow) => void;
}) {
  const [marking, setMarking] = useState(false);
  return (
    <>
      <Page>
        <Toolbar>
          <ToolbarTitle title="Tests" />
          <MoreMenu
            label="More"
            actions={[{ label: "Pass marks", onClick: () => setMarking(true) }]}
          />
        </Toolbar>
        <Composer target={scope.target} onAsk={onAsk} />
        {rows.length ? (
          <>
            <div className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
                <h2 className="m-0 min-w-0 flex-1 text-[19px] font-semibold tracking-[-0.01em]">
                  {count(rows.length, "test")}
                </h2>
                <RunAll going={going} onGoing={onGoing} onRun={onRunAll} />
              </div>
              <p className="m-0 text-[13px] text-ink-2" data-testid="tests-plays">
                {playsLine(scope.target, rows)}
              </p>
            </div>
            <Group>
              {rows.map((row) => (
                <TestRow
                  key={row.case}
                  row={row}
                  going={going}
                  onOpen={() => onOpen(row)}
                  onRun={() => onRun(row)}
                />
              ))}
            </Group>
          </>
        ) : null}
      </Page>
      {marking ? (
        <PassMarksDialog
          scope={scope}
          marks={passMarks}
          onClose={() => setMarking(false)}
          onSaved={async () => {
            await onPassMarks();
            setMarking(false);
          }}
        />
      ) : null}
    </>
  );
}
