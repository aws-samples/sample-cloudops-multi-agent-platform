"use client";

/**
 * Generic report exporters. Three formats:
 *
 *   - `markdown` — saves the raw `<report-body>` Markdown text (after stripping
 *     the agent preamble before the first heading).
 *   - `html` — wraps the rendered DOM in a standalone stylesheet and saves
 *     as a self-contained `.html` file. The embedded script keeps
 *     `data-chart-toggle` buttons functional offline.
 *   - `pdf` — opens the same HTML in a new tab and fires `window.print()`.
 *     The browser's native print dialog offers "Save as PDF" universally
 *     (Chrome, Safari, Firefox, Edge) with zero bundle weight. The opened
 *     tab closes after the user cancels or completes.
 *
 * Callers own the source material — pass the rendered DOM node + the raw
 * Markdown text + a title. The exporters take care of file naming,
 * preamble-stripping, and the HTML wrapper.
 */

export interface ExportReportOptions {
  /** Mounted DOM node whose rendered Markdown is being exported. */
  contentEl: HTMLElement | null;
  /** Raw Markdown body — used by `markdown` directly, printed into HTML as fallback when DOM is unavailable. */
  markdown: string;
  /** Report title; used for the HTML `<title>` and the filename stem. */
  title: string;
}

function safeFilename(title: string, fallback: string): string {
  return (
    title
      .replace(/[^a-zA-Z0-9 _-]/g, "")
      .trim()
      .replace(/\s+/g, "-")
      .toLowerCase() || fallback
  );
}

function stripPreamble(markdown: string): string {
  let text = markdown.trim();
  const headingIdx = text.search(/^(#{1,6}\s|---)/m);
  if (headingIdx > 0) text = text.slice(headingIdx);
  return text;
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Build the shared HTML wrapper used by both the HTML download and the PDF
 * print flow. Matches the original `ReportPanel` inline HTML byte-for-byte
 * so behaviour doesn't regress — keeps the smart-table chart/table toggles
 * working in the exported file.
 */
function buildStandaloneHtml(innerHtml: string, title: string): string {
  const safeTitle = title.replace(/</g, "&lt;");
  return `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${safeTitle}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'DM Sans',system-ui,sans-serif;color:#212529;background:#fff;padding:3rem 2rem;max-width:800px;margin:0 auto;line-height:1.7;font-size:15px;-webkit-font-smoothing:antialiased}
h1,h2,h3{font-weight:600;margin:1.5em 0 0.5em;color:#111}
h1{font-size:1.6em;border-bottom:1px solid #e9ecef;padding-bottom:0.4em}
h2{font-size:1.25em} h3{font-size:1.05em}
p{margin-bottom:0.75em}
ul,ol{margin-bottom:0.75em;padding-left:1.5em}
li{margin-bottom:0.25em}
strong{font-weight:600;color:#111}
a{color:#10b981;text-decoration:underline;text-underline-offset:2px}
code{background:#f1f3f5;padding:0.15em 0.4em;border-radius:4px;font-size:0.85em;font-family:'JetBrains Mono',monospace}
pre{background:#f8f9fa;border:1px solid #e9ecef;border-radius:8px;padding:1em;overflow-x:auto;margin:0.75em 0;font-size:0.85em}
pre code{background:transparent;padding:0}
table{width:100%;border-collapse:collapse;margin:0.75em 0;font-size:0.9em}
th{text-align:left;padding:0.6em 0.75em;border-bottom:2px solid #dee2e6;font-weight:600;color:#495057}
td{padding:0.5em 0.75em;border-bottom:1px solid #e9ecef}
hr{border:none;border-top:1px solid #e9ecef;margin:1.5em 0}
img{max-width:100%}
[data-chart-toggle]{cursor:pointer;display:inline-flex;align-items:center;gap:4px;font-size:12px;padding:4px 8px;border-radius:6px;border:none;color:#868e96;background:#f1f3f5}
[data-chart-toggle]:hover{background:#e9ecef}
@media print { body { padding: 1rem; max-width: 100%; } [data-chart-toggle] { display: none; } }
</style></head><body>${innerHtml}
<script>
function showView(el){el.style.position='';el.style.opacity='';el.style.pointerEvents='';el.style.zIndex='';el.style.width='';}
function hideView(el){el.style.position='absolute';el.style.opacity='0';el.style.pointerEvents='none';el.style.zIndex='-1';el.style.width='100%';}
document.querySelectorAll('[data-chart-toggle]').forEach(function(btn){
  btn.addEventListener('click',function(){
    var wrap=btn.closest('[data-smart-table]');
    if(!wrap)return;
    var chart=wrap.querySelector('[data-view="chart"]');
    var table=wrap.querySelector('[data-view="table"]');
    var label=btn.querySelector('[data-toggle-label]');
    if(!chart||!table)return;
    var chartVisible=chart.style.opacity!=='0';
    if(chartVisible){hideView(chart);showView(table);if(label)label.textContent='Chart';}
    else{showView(chart);hideView(table);if(label)label.textContent='Table';}
  });
});
</script></body></html>`;
}

function snapshotInnerHtml(contentEl: HTMLElement | null): string {
  if (!contentEl) return "";
  const clone = contentEl.cloneNode(true) as HTMLElement;
  // Strip agent preamble — drop text nodes / <p> elements preceding the first heading.
  for (const child of Array.from(clone.childNodes)) {
    if (child instanceof HTMLElement && /^H[1-6]$/.test(child.tagName)) break;
    clone.removeChild(child);
  }
  return clone.innerHTML;
}

export function exportReportAsMarkdown(opts: ExportReportOptions) {
  const text = stripPreamble(opts.markdown);
  if (!text) return;
  const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
  downloadBlob(blob, `${safeFilename(opts.title, "report")}.md`);
}

export function exportReportAsHtml(opts: ExportReportOptions) {
  const inner = snapshotInnerHtml(opts.contentEl);
  if (!inner) return;
  const html = buildStandaloneHtml(inner, opts.title);
  const blob = new Blob([html], { type: "text/html" });
  downloadBlob(blob, `${safeFilename(opts.title, "report")}.html`);
}

/**
 * PDF export: render the standalone HTML inside a hidden same-tab iframe,
 * then call `iframe.contentWindow.print()`. The browser's print dialog
 * offers "Save as PDF" universally (Chrome, Safari, Firefox, Edge), and
 * uses the iframe document's `<title>` as the default filename. We set
 * the title to `<filename>.pdf` so the saved file lands with the right
 * extension.
 *
 * Why iframe and not a popup window:
 *   - No popup blocker concerns.
 *   - No risk of the popup tab being treated as a downloadable HTML
 *     attachment by the browser before `print()` runs.
 *   - Print dialog is anchored to the parent tab — feels native.
 */
export function exportReportAsPdf(opts: ExportReportOptions) {
  const inner = snapshotInnerHtml(opts.contentEl);
  if (!inner) return;
  const printTitle = `${safeFilename(opts.title, "report")}.pdf`;
  const html = buildStandaloneHtml(inner, printTitle);

  // Build a hidden iframe and write the standalone HTML into it.
  const iframe = document.createElement("iframe");
  iframe.style.position = "fixed";
  iframe.style.right = "0";
  iframe.style.bottom = "0";
  iframe.style.width = "0";
  iframe.style.height = "0";
  iframe.style.border = "0";
  // `srcdoc` is the most reliable way to seed an iframe with a string
  // of HTML across browsers — it avoids same-origin quirks and the
  // race between document.write and the load event that the popup
  // approach was hitting.
  iframe.srcdoc = html;
  document.body.appendChild(iframe);

  let printed = false;
  const trigger = () => {
    if (printed) return;
    const w = iframe.contentWindow;
    const d = iframe.contentDocument;
    if (!w || !d) return;
    if (d.readyState === "loading") {
      setTimeout(trigger, 50);
      return;
    }
    printed = true;
    try { d.title = printTitle; } catch { /* same-origin guard */ }
    // Grace period for the Google Fonts link inside the iframe to
    // resolve. Printing before fonts load produces a serif-fallback PDF.
    setTimeout(() => {
      try {
        w.focus();
        w.print();
      } finally {
        // Tear down after the print dialog closes. Safari doesn't fire
        // `afterprint` on iframes reliably, so we just remove the
        // iframe a few seconds later — long enough for the dialog to
        // be visible, short enough not to leak DOM nodes.
        setTimeout(() => {
          if (iframe.parentNode) iframe.parentNode.removeChild(iframe);
        }, 1000);
      }
    }, 300);
  };

  // Both onload AND a polling fallback — onload fires reliably for
  // srcdoc iframes on modern browsers, but the polling fallback covers
  // the case where readyState transitions before onload binds.
  iframe.onload = trigger;
  setTimeout(trigger, 100);
}
