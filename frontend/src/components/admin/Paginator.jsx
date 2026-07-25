import React, { useEffect, useMemo, useState } from "react";

// Reusable client-side pagination for hand-rolled tables (the BackofficeUI DataTable has this
// built in; this is for the pages that don't use it).
//   const { pageItems, page, setPage, pageCount, total, from, to } = usePaged(guests, 25);
//   {guests view over pageItems}
//   <Paginator page={page} setPage={setPage} pageCount={pageCount} total={total} from={from} to={to} />
export function usePaged(items, pageSize = 25) {
  const list = items || [];
  const total = list.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const [page, setPage] = useState(0);

  useEffect(() => {
    if (page > pageCount - 1) setPage(0);
  }, [page, pageCount]);

  const pageItems = useMemo(
    () => list.slice(page * pageSize, page * pageSize + pageSize),
    [list, page, pageSize]
  );

  return {
    pageItems,
    page,
    setPage,
    pageCount,
    total,
    from: total === 0 ? 0 : page * pageSize + 1,
    to: Math.min(total, (page + 1) * pageSize),
  };
}

export function Paginator({ page, setPage, pageCount, total, from, to }) {
  if (pageCount <= 1) return null;
  return (
    <div className="flex items-center justify-between gap-3 px-6 py-3 border-t border-slate-700 text-sm text-slate-400">
      <span>Showing <b className="text-slate-200">{from}–{to}</b> of <b className="text-slate-200">{total}</b></span>
      <div className="flex items-center gap-2">
        <button
          onClick={() => setPage((p) => Math.max(0, p - 1))}
          disabled={page === 0}
          className="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-white disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          Prev
        </button>
        <span className="text-slate-300">Page {page + 1} / {pageCount}</span>
        <button
          onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
          disabled={page >= pageCount - 1}
          className="px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-white disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          Next
        </button>
      </div>
    </div>
  );
}
