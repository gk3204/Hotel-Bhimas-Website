// Single-series horizontal bar list (prompt 16). Magnitude of one measure across labelled
// categories/periods — gold-on-dark, rounded baseline-anchored ends, direct value labels, hover
// title. One series => no legend; color does not carry identity (per the dataviz method).
export default function BarList({ data = [], formatValue = (v) => v, max, empty = "No data." }) {
  if (!data.length) {
    return <p className="text-slate-500 text-sm py-6 text-center">{empty}</p>;
  }
  const peak = max ?? Math.max(1, ...data.map((d) => Math.abs(Number(d.value) || 0)));
  return (
    <div className="flex flex-col gap-3">
      {data.map((d) => {
        const val = Number(d.value) || 0;
        const pct = Math.min(100, Math.round((Math.abs(val) / peak) * 100));
        return (
          <div key={d.label} className="flex items-center gap-3" title={`${d.label}: ${formatValue(val)}`}>
            <div className="w-28 shrink-0 text-sm text-slate-300 truncate">{d.label}</div>
            <div className="flex-1 h-5 rounded-lg bg-slate-700/40 overflow-hidden">
              <div
                className="h-full rounded-lg bg-gradient-to-r from-[#E5C07B] to-[#D4AF37]"
                style={{ width: `${pct}%`, minWidth: val ? "6px" : "0" }}
              />
            </div>
            <div className="w-28 shrink-0 text-right text-sm font-semibold text-[#FCD34D]">
              {formatValue(val)}
              {d.sub ? <span className="text-slate-500 font-normal text-xs ml-1">{d.sub}</span> : null}
            </div>
          </div>
        );
      })}
    </div>
  );
}
