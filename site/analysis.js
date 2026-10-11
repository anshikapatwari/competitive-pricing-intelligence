(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const num = (v, d = 0) => (v == null ? "n/a" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const rs = (v) => (v == null ? "n/a" : "₹" + num(v, 0));
  const pct = (v, d = 0) => (v == null ? "n/a" : num(v, d) + "%");
  const prettify = (s) => s.replace(/&/g, " & ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/,(?=\S)/g, ", ");
  const BRAND_COLORS = ["#2874f0", "#ff9900", "#2e9e4f", "#8e44ad", "#d63384", "#00a3a3", "#b8860b", "#7f8c8d"];
  let charts = [], D = null;

  function table(id, cols, rows) {
    const t = $(id);
    t.innerHTML = "<thead><tr>" + cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.h)}</th>`).join("") + "</tr></thead><tbody>" +
      rows.map((r) => "<tr>" + cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.html ? c.f(r) : esc(c.f(r))}</td>`).join("") + "</tr>").join("") + "</tbody>";
  }
  const card = (k, v, s) => `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s || "")}</div></div>`;

  function theme() {
    const ink = css("--ink-2"), grid = css("--grid");
    Chart.defaults.color = ink; Chart.defaults.borderColor = grid;
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    return { ink, grid };
  }
  function chart(id, cfg) {
    const c = $(id); if (!c) return;
    cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false }, cfg.options || {});
    charts.push(new Chart(c, cfg));
  }

  function render() {
    charts.forEach((c) => c.destroy()); charts = [];
    theme();
    const m = D.meta, q = D.cleaning;
    $("eyebrow").textContent = `Amazon.in · ${num(m.n_products)} listings · snapshot ${m.snapshot_date || "date unknown"}`;
    $("cards").innerHTML = [
      card("Listings analysed", num(m.n_products), "unique products"),
      card("Headphone listings", num(m.n_headphones), "headphones, earbuds, accessories"),
      card("Median discount", pct(m.median_discount_all), "across all listings"),
      card("Sub-categories", num(m.n_categories), "with at least 15 listings"),
    ].join("");
    $("findings").innerHTML = D.findings.map((f) => `<li>${esc(f)}</li>`).join("");

    // categories
    const cats = D.categories;
    chart("c-cat", { type: "bar", data: { labels: cats.map((c) => prettify(c.sub) + ` (n=${c.n})`),
      datasets: [{ label: "Median discount (%)", data: cats.map((c) => c.median_discount),
        backgroundColor: cats.map((c) => (/Headphones/.test(c.sub) ? css("--s2") : css("--s1"))), borderRadius: 3 }] },
      options: { indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, max: 100, title: { display: true, text: "Median discount (%)" } } } } });
    const dist = D.distribution;
    chart("c-dist", { type: "bar", data: { labels: dist.labels, datasets: [
      { label: `Headphones (n=${dist.n_headphones})`, data: dist.headphones, backgroundColor: css("--s2"), borderRadius: 3 },
      { label: `Other electronics (n=${dist.n_other})`, data: dist.other_electronics, backgroundColor: css("--s1"), borderRadius: 3 }] },
      options: { scales: { y: { beginAtZero: true, title: { display: true, text: "Share of listings (%)" } } } } });
    $("dist-note").textContent = "Each bar shows the share of that group's listings falling in a discount band.";
    table("t-cat", [
      { h: "Sub-category", f: (c) => prettify(c.sub) }, { h: "Listings", num: true, f: (c) => num(c.n) },
      { h: "Median price", num: true, f: (c) => rs(c.median_price) }, { h: "Median MRP", num: true, f: (c) => rs(c.median_mrp) },
      { h: "Median discount", num: true, f: (c) => pct(c.median_discount) }, { h: "Discounted 60%+", num: true, f: (c) => pct(c.share_ge_60) },
      { h: "Median rating", num: true, f: (c) => num(c.median_rating, 2) }], cats);

    // brands
    const br = D.brands;
    $("brand-note").textContent = `Based on ${m.n_headphones} unique listings. Brands with fewer than three listings are grouped as "Other brands", and small groups should be read with caution.`;
    const names = br.map((b) => b.brand), col = (b) => (b === "Other brands" ? "#7f8c8d" : BRAND_COLORS[names.filter((n) => n !== "Other brands").indexOf(b) % BRAND_COLORS.length]);
    const byBrand = {};
    D.points.forEach((p) => { (byBrand[p.brand] = byBrand[p.brand] || []).push(p); });
    chart("c-value", { type: "bubble", data: { datasets: Object.keys(byBrand).map((b) => ({ label: b,
      data: byBrand[b].map((p) => ({ x: p.price, y: p.rating, r: Math.max(4, Math.min(18, Math.sqrt((p.ratings || 0) / 400))), p })),
      backgroundColor: col(b) + "aa", borderColor: col(b) })) },
      options: { scales: { x: { title: { display: true, text: "Selling price (₹)" }, beginAtZero: true }, y: { title: { display: true, text: "Rating (out of 5)" }, min: 2.5, max: 5 } },
        plugins: { tooltip: { callbacks: { label: (c) => { const p = c.raw.p; return [p.name, `${rs(p.price)} · ${num(p.rating, 1)}★ · ${num(p.ratings)} ratings · ${pct(p.discount)} off`]; } } } } } });
    chart("c-brand-price", { type: "bar", data: { labels: names, datasets: [
      { label: "Median selling price", data: br.map((b) => b.median_price), backgroundColor: css("--s1"), borderRadius: 3 },
      { label: "Median MRP", data: br.map((b) => b.median_mrp), backgroundColor: css("--s2"), borderRadius: 3 }] },
      options: { scales: { y: { beginAtZero: true, title: { display: true, text: "₹" } } } } });
    chart("c-brand-disc", { type: "bar", data: { labels: names, datasets: [{ label: "Median discount (%)", data: br.map((b) => b.median_discount), backgroundColor: br.map((b) => col(b.brand)), borderRadius: 3 }] },
      options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, max: 100, title: { display: true, text: "%" } } } } });
    table("t-brands", [
      { h: "Brand", f: (b) => b.brand }, { h: "Listings", num: true, f: (b) => num(b.n) },
      { h: "Median price", num: true, f: (b) => rs(b.median_price) }, { h: "Median MRP", num: true, f: (b) => rs(b.median_mrp) },
      { h: "Median discount", num: true, f: (b) => pct(b.median_discount) }, { h: "Median rating", num: true, f: (b) => num(b.median_rating, 2) },
      { h: "Total ratings", num: true, f: (b) => num(b.ratings) }], br);
    table("t-bands", [
      { h: "Price band", f: (b) => b.band }, { h: "Listings", num: true, f: (b) => num(b.n) },
      { h: "Median discount", num: true, f: (b) => pct(b.median_discount) }, { h: "Median rating", num: true, f: (b) => num(b.median_rating, 2) },
      { h: "Median rating count", num: true, f: (b) => num(b.median_ratings) }], D.bands);

    // discounts vs ratings
    const w = D.within_category.filter((x) => x.rho != null);
    chart("c-within", { type: "bar", data: { labels: w.map((x) => prettify(x.sub) + ` (n=${x.n})`), datasets: [
      { label: "95% interval", data: w.map((x) => [x.lo, x.hi]), backgroundColor: css("--s1") + "55", borderRadius: 3, barThickness: 14 },
      { type: "line", label: "Spearman rho", data: w.map((x) => x.rho), showLine: false, pointStyle: "rectRot", pointRadius: 6, borderColor: css("--s2"), backgroundColor: css("--s2") }] },
      options: { indexAxis: "y", scales: { x: { min: -0.7, max: 0.5, title: { display: true, text: "Rank correlation (discount vs. log rating count)" } } } } });
    const A = D.association, lab = {
      all_discount_vs_ratings: "Discount vs. rating count (all listings)", all_discount_vs_rating: "Discount vs. rating (all listings)",
      headphones_discount_vs_ratings: "Discount vs. rating count (headphones)", headphones_price_vs_rating: "Selling price vs. rating (headphones)" };
    table("t-assoc", [
      { h: "Relationship", f: (r) => lab[r.k] }, { h: "Listings", num: true, f: (r) => num(r.n) },
      { h: "Spearman rho", num: true, f: (r) => (r.rho == null ? "n/a" : (r.rho >= 0 ? "+" : "") + num(r.rho, 2)) },
      { h: "95% interval", num: true, f: (r) => (r.rho == null ? "n/a" : `${num(r.lo, 2)} to ${num(r.hi, 2)}`) },
      { h: "Interval includes zero", f: (r) => (r.rho == null ? "n/a" : r.lo <= 0 && r.hi >= 0 ? "Yes" : "No") }],
      Object.keys(A).map((k) => Object.assign({ k }, A[k])));

    // method
    $("q-cards").innerHTML = [
      card("Raw rows", num(q.raw_rows)), card("Duplicate rows removed", num(q.duplicate_product_rows), "repeated product IDs"),
      card("Unique products", num(q.unique_products)), card("Discount mismatches", num(q.discount_mismatch_gt_1pp), "stated vs. recomputed, over 1 pp"),
    ].join("");
    table("t-quality", [{ h: "Check", f: (r) => r[0] }, { h: "Rows", num: true, f: (r) => num(r[1]) }], [
      ["Raw rows in file", q.raw_rows], ["Repeated product IDs (collapsed)", q.duplicate_product_rows],
      ["Unparseable rating", q.unparseable_rating], ["Missing rating count", q.missing_rating_count],
      ["Selling price above MRP", q.price_gt_mrp], ["Stated discount differs from recomputed by more than 1 pp", q.discount_mismatch_gt_1pp]]);
    $("foot-meta").textContent = `Source: ${m.source} · snapshot ${m.snapshot_date || "unknown"}`;
  }

  function initTheme() {
    const root = document.documentElement;
    try { const t = localStorage.getItem("theme"); if (t) root.dataset.theme = t; } catch (e) {}
    $("theme").addEventListener("click", () => {
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
      if (D) render();
    });
  }

  initTheme();
  fetch("data/amazon_analysis.json").then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }).then((j) => {
    D = j; $("loading").hidden = true; render();
  }).catch((e) => { $("loading").hidden = true; const n = $("load-error"); n.hidden = false; n.textContent = "The analysis data could not be loaded (" + e.message + ")."; });
})();
