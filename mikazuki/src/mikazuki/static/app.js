// Confirm destructive forms, and Ctrl/Cmd+S to save in the editor. No inline scripts (CSP).
document.addEventListener("submit", (e) => {
  const msg = e.target.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) e.preventDefault();
});
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    const form = document.querySelector("form.editor");
    if (form) { e.preventDefault(); form.requestSubmit(); }
  }
  if (e.key === "Tab" && e.target.matches("form.editor textarea")) {
    e.preventDefault();
    const t = e.target, s = t.selectionStart;
    t.setRangeText("  ", s, t.selectionEnd, "end");
  }
});
// Copy an answer to the clipboard (Apply page).
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-copy]");
  if (!b) return;
  const t = document.getElementById(b.dataset.copy);
  if (!t) return;
  try { await navigator.clipboard.writeText(t.value); }
  catch { t.select(); document.execCommand("copy"); }
  const label = b.textContent;
  b.textContent = "Copied";
  setTimeout(() => { b.textContent = label; }, 1200);
});
// data-confirm on a submit button (e.g. Redraft), not only on forms.
document.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-confirm]");
  if (b && !window.confirm(b.dataset.confirm)) e.preventDefault();
});
