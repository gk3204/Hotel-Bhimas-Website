import { useMemo, useState } from "react";

/**
 * Table sorting, shared by every admin list (v5s).
 *
 * `DataTable` (components/admin/BackofficeUI) has sorting built in, but a dozen admin pages hand-roll
 * their own <table> with bespoke cells — status pills, nested detail lines, action buttons — and their
 * own padding. Replacing all that markup just to gain a click would risk a visual regression on each
 * page, so the SORTING lives here and those tables adopt it in place. One comparator, one asc → desc →
 * off cycle, one indicator, whichever table you are looking at.
 *
 * Lives in utils/ rather than beside the components so the hook and the comparator are not
 * non-component exports from a component module.
 */

/**
 * Compare two cell values: numbers numerically, everything else as natural, case-insensitive
 * strings, nulls last.
 *
 * `numeric: true` is the part that matters for this hotel: room numbers are strings, so a plain
 * comparison puts "10" before "9". Natural collation gets 9, 10, 11 right without the caller
 * needing to know the column holds numbers in a text field.
 */
export const cmpValues = (a, b) => {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
};

/**
 * Sort state over a row list.
 *
 *   const sorted = useTableSort(rooms);
 *   <SortTh ctx={sorted} k="room_number">Room</SortTh>
 *   {sorted.rows.map(...)}
 *
 * The active column is tracked by a STABLE STRING key, never by the accessor function: an inline
 * `(r) => ...` is a new object on every render, so comparing function identity would make the
 * active-column highlight and the asc/desc toggle silently stop working.
 */
export function useTableSort(rows) {
  const [sort, setSort] = useState({ key: null, dir: "asc", get: null });

  const sortedRows = useMemo(() => {
    const base = rows || [];
    if (!sort.key) return base;                    // "off" = whatever order the server sent
    const acc = sort.get || ((r) => r?.[sort.key]);
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...base].sort((a, b) => cmpValues(acc(a), acc(b)) * factor);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, sort.key, sort.dir]);

  const toggle = (key, get) =>
    setSort((s) => {
      if (s.key !== key) return { key, dir: "asc", get: get || null };
      if (s.dir === "asc") return { key, dir: "desc", get: get || null };
      return { key: null, dir: "asc", get: null };  // third click restores the server's order
    });

  return { rows: sortedRows, sort, toggle };
}
