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

/**
 * The tests: what to test next (handed to the test writer), Run all, then every test with its
 * latest verdict. Pass marks sit in the header's menu.
 */
export function TestsView({
  scope,
  rows,
  passMarks,
  stamp,
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
  /** Re-estimate Run all when this changes. */
  stamp: string;
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
      <Toolbar>
        <ToolbarTitle title="Tests" />
        <MoreMenu
          label="More"
          actions={[{ label: "Pass marks", onClick: () => setMarking(true) }]}
        />
      </Toolbar>
      <Page>
        <Composer target={scope.target} onAsk={onAsk} />
        {rows.length ? (
          <>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
              <h2 className="m-0 min-w-0 flex-1 text-[17px] font-semibold tracking-[-0.2px]">
                {count(rows.length, "test")}
              </h2>
              <RunAll
                scope={scope}
                tests={rows.length}
                stamp={stamp}
                going={going}
                onGoing={onGoing}
                onRun={onRunAll}
              />
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
