// v7c (owner, 12 Oct): on a phone, a table wider than the screen scrolls sideways inside its box -
// but nothing said so, so the columns past the edge looked missing. This puts a one-line
// "swipe for more columns" hint above every such table, and takes it away when the table fits
// (rotating the phone, a wider screen). One observer for the whole app - no page needs changing.
const HINT = "table-swipe-hint";

function scrollerOf(table) {
  let el = table.parentElement;
  while (el && el !== document.body) {
    const ox = getComputedStyle(el).overflowX;
    if (ox === "auto" || ox === "scroll") return el;
    el = el.parentElement;
  }
  return null;
}

function refresh() {
  const small = window.innerWidth <= 768;
  document.querySelectorAll("table").forEach((t) => {
    const sc = scrollerOf(t);
    if (!sc) return;
    const prev = sc.previousElementSibling;
    const has = prev && prev.classList && prev.classList.contains(HINT);
    const wide = small && sc.scrollWidth > sc.clientWidth + 4 && t.offsetParent !== null;
    if (wide && !has) {
      const d = document.createElement("div");
      d.className = HINT;
      d.textContent = "← swipe for more columns →";
      sc.parentElement.insertBefore(d, sc);
    } else if (!wide && has) {
      prev.remove();
    }
  });
}

export function installTableScrollHints() {
  if (typeof window === "undefined" || window.__tableHints) return;
  window.__tableHints = true;
  let pending = 0;
  const schedule = () => {
    if (pending) return;
    pending = window.setTimeout(() => { pending = 0; refresh(); }, 250);
  };
  new MutationObserver((muts) => {
    // ignore our own hint insertions
    if (muts.every((m) => [...m.addedNodes, ...m.removedNodes].every((n) => n.classList && n.classList.contains(HINT)))) return;
    schedule();
  }).observe(document.body, { childList: true, subtree: true });
  window.addEventListener("resize", schedule);
  schedule();
}
