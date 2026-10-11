/* Pricing Intelligence dashboard. Static page: reads data/dashboard.json (or demo.json with ?demo=1). */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const VIEWS = ["overview", "tracker", "elasticity", "positioning", "method"];
  const params = new URLSearchParams(location.search);
  const DEMO = params.get("demo") === "1";
  const DATA_URL = DEMO ? "data/demo.json" : "data/dashboard.json";

  let D = null;                       // loaded document
  let byId = {};                      // product id -> product
  const F = { platform: "", brand: "", from: "", to: "" };
  const charts = {};

  // ---------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const num = (v, d = 0) => (v == null || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }));
  const inr = (v) => (v == null ? "–" : "₹" + num(v, 0));
  const pct = (v, d = 1) => (v == null ? "–" : num(v, d) + "%");
  const platColor = (p) => (p === "flipkart" ? css("--s1") : css("--s2"));
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const filtersActive = () => !!(F.platform || F.brand || F.from || F.to);

  function visibleIds() {
    const s = new Set();
    for (const p of D.products) if ((!F.platform || p.platform === F.platform) && (!F.brand || p.brand === F.brand)) s.add(p.id);
    return s;
  }
  function dateIdx() {
    const out = [];
    D.dates.forEach((d, i) => { if ((!F.from || d >= F.from) && (!F.to || d <= F.to)) out.push(i); });
    return out;
  }
  const inRange = (d) => (!F.from || d >= F.from) && (!F.to || d <= F.to);

  function chart(id, cfg) {
    if (charts[id]) charts[id].destroy();
    const el = $(id);
    if (!el || typeof Chart === "undefined") return null;
    const grid = css("--grid"), ink = css("--ink-2");
    Chart.defaults.color = ink;
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    cfg.options = cfg.options || {};
    cfg.options.responsive = true;
    cfg.options.maintainAspectRatio = false;
    for (const ax of Object.values(cfg.options.scales || {})) {
      ax.grid = { color: grid, ...(ax.grid || {}) };
      ax.border = { color: grid };
    }
    charts[id] = new Chart(el, cfg);
    return charts[id];
  }
  function ols(pts) {
    const n = pts.length;
    if (n < 3) return null;
    const mx = pts.reduce((a, p) => a + p.x, 0) / n, my = pts.reduce((a, p) => a + p.y, 0) / n;
    let sxx = 0, sxy = 0;
    for (const p of pts) { sxx += (p.x - mx) ** 2; sxy += (p.x - mx) * (p.y - my); }
    if (sxx === 0) return null;
    const slope = sxy / sxx;
    return { slope, intercept: my - slope * mx };
  }
  function table(id, cols, rows, emptyMsg) {
    const t = $(id);
    const head = "<thead><tr>" + cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.h)}</th>`).join("") + "</tr></thead>";
    const body = rows.length
      ? rows.map((r) => "<tr>" + cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.html ? c.f(r) : esc(c.f(r))}</td>`).join("") + "</tr>").join("")
      : `<tr><td class="empty" colspan="${cols.length}">${esc(emptyMsg)}</td></tr>`;
    t.innerHTML = head + "<tbody>" + body + "</tbody>";
  }
  const card = (k, v, s = "", extra = "") => `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div>${extra}</div>`;

  // ---------- views
  function renderOverview() {
    const m = D.meta;
    const prog = Math.min(100, (m.window_days / m.target_days) * 100);
    $("cards").innerHTML = [
      card("Products tracked", num(m.n_products), (m.platforms || []).map(cap).join(" + ")),
      card("Valid snapshots", num(m.n_snapshots), "after cleaning"),
      card("Collection window", num(m.window_days, 1) + " days", `target ${m.target_days} days`, `<div class="bar" aria-hidden="true"><i style="width:${prog}%"></i></div>`),
      card("Discount changes", num(m.n_discount_changes), "excl. first snapshot of each product"),
      card("Average discount", pct(m.avg_discount), "across all valid snapshots"),
      card("Matched pairs", num(m.n_matched_products), "same model on both platforms"),
    ].join("");

    const f = [], e = D.elasticity, P = D.positioning;
    if (e.available && e.fit_deeper && e.fit_deeper.available) {
      const s = e.fit_deeper, ci = s.slope_ci95;
      f.push(`Across <b>${num(s.n)}</b> deeper-discount events, every extra 10 points of discount was associated with <b>${num(s.slope * 10, 1)}</b> more new ratings per day (95% bootstrap CI ${num(ci[0] * 10, 1)} to ${num(ci[1] * 10, 1)}; r = ${num(s.r, 2)}).`);
    }
    if (e.available && e.floor && e.floor.available) {
      const fl = e.floor;
      f.push(`Estimated <b>discount floor ≈ ${num(fl.floor_pct, 0)}%</b>: below it the mean velocity lift was ${pct(fl.mean_below * 100, 0)} (n=${fl.n_below}); above it ${pct(fl.mean_above * 100, 0)} (n=${fl.n_above}). Permutation p = ${num(fl.p_value, 3)}${fl.p_value > 0.05 ? " (not distinguishable from noise at this sample size)" : ""}.`);
    }
    if (e.available && e.by_price_band && e.by_price_band.length) {
      const b = [...e.by_price_band].filter((x) => x.median_lift != null).sort((a, c) => c.median_lift - a.median_lift)[0];
      if (b) f.push(`Largest median demand lift is in the <b>${esc(b.segment)}</b> price band (${pct(b.median_lift * 100, 0)}, n=${b.n}).`);
    }
    if (e.available && e.n_erosion > 0) f.push(`<b>${num(e.n_erosion)}</b> deep discounts (≥30%) were followed by little or no rise in review velocity.`);
    if (P.unexplained_gaps && P.unexplained_gaps.length) {
      const g = P.unexplained_gaps[0];
      f.push(`Largest unexplained cross-platform gap: <b>${esc(g.product_name)}</b>, ${pct(Math.abs(g.avg_gap_pct))} dearer on ${esc(g.pricier_platform)} with no rating edge.`);
    }
    if (P.response_lag && P.response_lag.n_observable > 0) {
      const r = P.response_lag;
      f.push(`When one platform cut a matched price by ≥${r.min_drop_pct}%, the other followed within ${r.horizon_days} days in <b>${pct((r.n_responded / r.n_observable) * 100, 0)}</b> of ${num(r.n_observable)} observable cases${r.median_lag_days != null ? ` (median lag ${num(r.median_lag_days, 1)} days)` : ""}.`);
    }
    if (!f.length) f.push(`Not enough data yet. An elasticity estimate needs discount changes that have 7 days of review history on both sides, so expect the first findings after about 2–3 weeks of daily collection.`);
    $("findings").innerHTML = f.map((x) => `<li>${x}</li>`).join("");
  }

  function renderTracker() {
    const ids = visibleIds(), di = dateIdx();
    const prods = D.products.filter((p) => ids.has(p.id)).sort((a, b) => a.name.localeCompare(b.name));
    const sel = $("product-select");
    const cur = sel.value;
    sel.innerHTML = prods.length ? prods.map((p) => `<option value="${esc(p.id)}">${esc(p.name)} · ${esc(cap(p.platform))}</option>`).join("") : "<option value=''>No products match</option>";
    if (prods.some((p) => p.id === cur)) sel.value = cur;
    drawPrice();

    // average discount per platform per day, computed from the filtered products
    const plats = [...new Set(prods.map((p) => p.platform))].sort();
    const ds = plats.map((pl) => {
      const mine = prods.filter((p) => p.platform === pl);
      return {
        label: cap(pl), borderColor: platColor(pl), backgroundColor: platColor(pl), borderWidth: 2, pointRadius: 2, tension: 0.2, spanGaps: true,
        data: di.map((i) => {
          const v = mine.map((p) => D.series[p.id].discount[i]).filter((x) => x != null);
          return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
        }),
      };
    });
    chart("c-platform", {
      type: "line", data: { labels: di.map((i) => D.dates[i]), datasets: ds },
      options: { interaction: { mode: "index", intersect: false }, scales: { y: { title: { display: true, text: "Avg discount (%)" }, beginAtZero: true }, x: { ticks: { maxTicksLimit: 8 } } },
        plugins: { legend: { display: ds.length > 1 } } },
    });

    const names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
    let wk;
    if (!filtersActive()) wk = D.cadence.weekday.map((w) => w.events);
    else {
      wk = [0, 0, 0, 0, 0, 0, 0];
      for (const ev of D.events) if (ids.has(ev.id) && inRange(ev.date) && ev.change > 0) wk[(new Date(ev.date + "T00:00:00Z").getUTCDay() + 6) % 7]++;
    }
    chart("c-weekday", {
      type: "bar", data: { labels: names.map((n) => n.slice(0, 3)), datasets: [{ label: "Deeper-discount events", data: wk, backgroundColor: css("--s1"), borderRadius: 4, maxBarThickness: 36 }] },
      options: { scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }, plugins: { legend: { display: false } } },
    });

    // heatmap
    const rows = prods.slice(0, 150);
    const max = Math.max(1, ...rows.flatMap((p) => di.map((i) => D.series[p.id].discount[i] ?? 0)));
    const head = "<thead><tr><th></th>" + di.map((i) => `<th scope="col">${esc(D.dates[i].slice(5))}</th>`).join("") + "</tr></thead>";
    const body = rows.map((p) => `<tr><th scope="row" title="${esc(p.name)}">${esc(p.name)} · ${esc(cap(p.platform))}</th>` + di.map((i) => {
      const v = D.series[p.id].discount[i];
      if (v == null) return `<td title="${esc(p.name)} · ${esc(D.dates[i])}: no snapshot"></td>`;
      return `<td style="background:color-mix(in srgb, var(--seq-700) ${Math.round((v / max) * 100)}%, var(--seq-100))" title="${esc(p.name)} · ${esc(D.dates[i])}: ${num(v, 1)}% off"></td>`;
    }).join("") + "</tr>").join("");
    $("heatmap").innerHTML = rows.length && di.length ? `<table class="heat">${head}<tbody>${body}</tbody></table>` : `<p class="muted" style="padding:16px">No products or dates match the current filters.</p>`;

    const styleOrder = { volatile: 0, steady: 1, "rarely-discounts": 2 };
    const brands = D.cadence.brand_typology.filter((b) => !F.brand || b.brand === F.brand).sort((a, b) => styleOrder[a.style] - styleOrder[b.style] || b.avg_discount - a.avg_discount);
    table("t-brands", [
      { h: "Brand", f: (r) => r.brand }, { h: "Style", f: (r) => `<span class="pill">${esc(r.style)}</span>`, html: true },
      { h: "Avg depth", num: true, f: (r) => pct(r.avg_discount) }, { h: "Std dev (pp)", num: true, f: (r) => num(r.discount_std, 1) },
      { h: "Max", num: true, f: (r) => pct(r.max_discount, 0) }, { h: "Changes / product", num: true, f: (r) => num(r.changes_per_product, 1) },
      { h: "Products", num: true, f: (r) => num(r.n_products) },
    ], brands, "No brand statistics yet.");

    const ev = D.events.filter((e) => ids.has(e.id) && inRange(e.date)).slice(0, 25);
    table("t-events", [
      { h: "Date", f: (r) => r.date }, { h: "Product", f: (r) => r.name }, { h: "Platform", f: (r) => cap(r.platform) },
      { h: "Discount", num: true, f: (r) => `${num(r.from, 1)}% → ${num(r.to, 1)}%` },
      { h: "Change", num: true, f: (r) => (r.change > 0 ? "+" : "") + num(r.change, 1) + " pp" }, { h: "Price", num: true, f: (r) => inr(r.price) },
    ], ev, "No discount changes recorded for the current filters.");
  }

  function drawPrice() {
    const id = $("product-select").value;
    const p = byId[id], s = id && D.series[id];
    const note = $("price-note");
    if (!p || !s) { if (charts["c-price"]) { charts["c-price"].destroy(); delete charts["c-price"]; } note.textContent = "Select a product to see its price history."; return; }
    const di = dateIdx();
    const col = platColor(p.platform);
    chart("c-price", {
      type: "line",
      data: { labels: di.map((i) => D.dates[i]), datasets: [
        { label: "Selling price", data: di.map((i) => s.price[i]), borderColor: col, backgroundColor: col, borderWidth: 2, pointRadius: 3, stepped: true, spanGaps: false },
        { label: "MRP", data: di.map((i) => s.mrp[i]), borderColor: css("--ink-3"), borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0, stepped: true, spanGaps: false },
      ] },
      options: { interaction: { mode: "index", intersect: false },
        scales: { y: { title: { display: true, text: "₹" }, ticks: { callback: (v) => num(v) } }, x: { ticks: { maxTicksLimit: 10 } } },
        plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${inr(c.parsed.y)}` } } } },
    });
    note.textContent = `${p.name} · ${cap(p.platform)} · ${p.brand} · ${num(p.n_changes)} discount changes, avg ${pct(p.avg_discount)}. Gaps in the line are scrape gaps, not price changes.`;
  }

  function renderElasticity() {
    const e = D.elasticity;
    const emp = $("el-empty"), body = $("el-body");
    if (!e.available) { emp.hidden = false; body.hidden = true; emp.textContent = e.reason || "Not enough data yet."; return; }
    emp.hidden = true; body.hidden = false;
    const ids = visibleIds();
    const pts = e.points.filter((p) => ids.has(p.id) && inRange(p.date));
    const fit = ols(pts);
    const fl = e.floor && e.floor.available ? e.floor : null;
    const fd = e.fit_deeper;
    $("el-cards").innerHTML = [
      card("Events analysed", num(e.n_deeper), `${num(e.n_clean_window)} with no overlapping change`),
      card("Slope (per 10 pp)", fd && fd.available ? `${num(fd.slope * 10, 1)} /day` : "n/a", fd && fd.available ? `95% CI ${num(fd.slope_ci95[0] * 10, 1)} to ${num(fd.slope_ci95[1] * 10, 1)}` : (fd ? fd.reason : "")),
      card("Discount floor", fl ? num(fl.floor_pct, 0) + "%" : "n/a", fl ? `permutation p = ${num(fl.p_value, 3)}` : (e.floor ? e.floor.reason : "")),
      card("Not paying for themselves", num(e.n_erosion), "≥30% off, ≤10% lift"),
    ].join("");

    const bandCol = { budget: css("--s1"), mid: css("--s2"), premium: css("--s3") };
    const sets = ["budget", "mid", "premium"].map((b) => {
      const mine = pts.filter((p) => p.band === b);
      return { label: cap(b), data: mine.map((p) => ({ x: p.x, y: p.y, p })), backgroundColor: mine.map((p) => (p.overlap ? bandCol[b] + "33" : bandCol[b])),
        borderColor: bandCol[b], borderWidth: 1.5, pointRadius: 5, pointHoverRadius: 7 };
    });
    if (fit && pts.length) {
      const xs = pts.map((p) => p.x), x0 = Math.min(...xs), x1 = Math.max(...xs);
      sets.push({ type: "line", label: `Linear fit (slope ${num(fit.slope * 10, 1)} per 10 pp)`, data: [{ x: x0, y: fit.intercept + fit.slope * x0 }, { x: x1, y: fit.intercept + fit.slope * x1 }],
        borderColor: css("--ink"), borderWidth: 2, pointRadius: 0 });
    }
    if (fl && pts.length) {
      const ys = pts.map((p) => p.y);
      sets.push({ type: "line", label: `Estimated floor ≈ ${num(fl.floor_pct, 0)}%`, data: [{ x: fl.floor_pct, y: Math.min(...ys) }, { x: fl.floor_pct, y: Math.max(...ys) }],
        borderColor: css("--ink-3"), borderDash: [6, 4], borderWidth: 2, pointRadius: 0 });
    }
    chart("c-scatter", {
      type: "scatter", data: { datasets: sets },
      options: { scales: { x: { title: { display: true, text: "Discount depth after the change (%)" }, min: 0 },
                           y: { title: { display: true, text: "Change in review velocity (new ratings / day)" } } },
        plugins: { tooltip: { callbacks: { label: (c) => {
          const p = c.raw.p; if (!p) return c.dataset.label;
          const prod = byId[p.id];
          return [`${prod ? prod.name : p.id} (${p.brand})`, `${num(p.x, 0)}% off · Δ velocity ${num(p.y, 1)}/day`, `lift ${p.lift == null ? "–" : pct(p.lift * 100, 0)} · ${p.date}${p.overlap ? " · overlapping change" : ""}`];
        } } } } },
    });
    $("scatter-note").textContent = `${pts.length} events shown${filtersActive() ? " (filters on; fit recomputed on shown events)" : ""}. Faded points have another discount change inside their ±7-day window, so their before/after is contaminated.`;

    const bar = (id, rows, order) => {
      const r = order.map((k) => rows.find((x) => x.segment === k)).filter(Boolean);
      chart(id, { type: "bar", data: { labels: r.map((x) => `${cap(x.segment)} (n=${x.n})`), datasets: [{ label: "Median velocity lift (%)", data: r.map((x) => (x.median_lift == null ? null : x.median_lift * 100)), backgroundColor: css("--s1"), borderRadius: 4, maxBarThickness: 56 }] },
        options: { scales: { y: { title: { display: true, text: "Median lift (%)" } } }, plugins: { legend: { display: false } } } });
    };
    bar("c-band", e.by_price_band || [], ["budget", "mid", "premium"]);
    bar("c-tier", e.by_brand_tier || [], ["niche", "challenger", "major"]);

    const er = e.erosion.filter((r) => ids.has(r.id) && inRange(r.date));
    table("t-erosion", [
      { h: "Date", f: (r) => r.date }, { h: "Product", f: (r) => (byId[r.id] ? byId[r.id].name : r.id) }, { h: "Brand", f: (r) => r.brand },
      { h: "Discount", num: true, f: (r) => pct(r.discount_pct, 0) }, { h: "Velocity before", num: true, f: (r) => num(r.before, 1) + "/day" },
      { h: "after", num: true, f: (r) => num(r.after, 1) + "/day" }, { h: "Avg price", num: true, f: (r) => inr(r.avg_price) },
    ], er, "No flagged events for the current filters.");
  }

  function renderPositioning() {
    const ids = visibleIds(), P = D.positioning;
    const pts = P.value_map.filter((p) => ids.has(p.id));
    const maxR = Math.max(1, ...pts.map((p) => p.reviews || 0));
    const plats = [...new Set(pts.map((p) => p.platform))].sort();
    chart("c-value", {
      type: "bubble",
      data: { datasets: plats.map((pl) => ({ label: cap(pl), backgroundColor: platColor(pl) + "99", borderColor: platColor(pl), borderWidth: 1.5,
        data: pts.filter((p) => p.platform === pl).map((p) => ({ x: p.price, y: p.rating, r: 4 + 16 * Math.sqrt((p.reviews || 0) / maxR), p })) })) },
      options: { scales: { x: { title: { display: true, text: "Average selling price (₹)" }, ticks: { callback: (v) => num(v) } },
                           y: { title: { display: true, text: "Average rating" }, suggestedMin: 3, suggestedMax: 5 } },
        plugins: { tooltip: { callbacks: { label: (c) => { const p = c.raw.p; return [`${p.name} (${p.brand})`, `${inr(p.price)} · rating ${num(p.rating, 2)} · ${num(p.reviews)} ratings`]; } } } } },
    });

    const pairs = P.matched_pairs.filter((x) => (!F.brand || x.brand === F.brand));
    const keep = new Set(pairs.map((x) => x.match_id));
    const byDate = {};
    for (const g of P.daily_gap) if (inRange(g.date) && keep.has(g.match_id)) (byDate[g.date] = byDate[g.date] || []).push(g.gap);
    const dates = Object.keys(byDate).sort();
    const a = pairs[0] ? cap(pairs[0].platform_a) : "A", b = pairs[0] ? cap(pairs[0].platform_b) : "B";
    chart("c-gap", {
      type: "line", data: { labels: dates, datasets: [{ label: `${a} − ${b} (₹)`, data: dates.map((d) => byDate[d].reduce((x, y) => x + y, 0) / byDate[d].length),
        borderColor: css("--s1"), backgroundColor: css("--s1"), borderWidth: 2, pointRadius: 2, tension: 0.2 }] },
      options: { scales: { y: { title: { display: true, text: `₹ (positive = ${a} dearer)` } }, x: { ticks: { maxTicksLimit: 8 } } }, plugins: { legend: { display: false } } },
    });

    const r = P.response_lag, fm = P.first_mover;
    const wins = Object.entries(fm.first_discount_wins || {}).map(([k, v]) => `${cap(k)} ${v}`).join(" · ");
    $("lag-panel").innerHTML = `<div class="t" style="font-weight:600;margin-bottom:8px">Competitive response</div>` +
      (r.n_observable > 0
        ? `<p class="stat-line"><b>${num(r.n_responded)}</b> of <b>${num(r.n_observable)}</b> observable price cuts (≥${r.min_drop_pct}%) were matched on the other platform within ${r.horizon_days} days${r.median_lag_days != null ? `; median lag <b>${num(r.median_lag_days, 1)} days</b>` : ""}.</p>`
        : `<p class="muted">No observable matched price cuts yet.</p>`) +
      (fm.pairs ? `<p class="stat-line">First platform to show a deeper discount across ${num(fm.pairs)} matched products: ${esc(wins)}.</p>` : "") +
      `<p class="muted small">Drops without a full ${r.horizon_days}-day follow-up window are excluded (right-censored), not counted as “no response”.</p>`;

    const gaps = P.unexplained_gaps.filter((g) => !F.brand || g.brand === F.brand).slice(0, 15);
    table("t-gaps", [
      { h: "Product", f: (g) => g.product_name }, { h: "Brand", f: (g) => g.brand }, { h: "Dearer on", f: (g) => cap(g.pricier_platform) },
      { h: "Avg gap", num: true, f: (g) => inr(Math.abs(g.avg_gap)) }, { h: "Gap %", num: true, f: (g) => pct(Math.abs(g.avg_gap_pct)) },
      { h: "Rating edge of dearer", num: true, f: (g) => (g.pricier_rating_edge == null ? "–" : (g.pricier_rating_edge >= 0 ? "+" : "") + num(g.pricier_rating_edge, 2)) },
      { h: "Days compared", num: true, f: (g) => num(g.days_compared) },
    ], gaps, "No unexplained gaps. Either no matched products are available yet, or every gap is small or justified by rating.");
    $("rank-note").textContent = P.search_rank.reason;
  }

  function renderMethod() {
    const q = D.quality, c = q.cleaning;
    $("q-cards").innerHTML = [
      card("Raw rows", num(c.raw_rows)), card("Kept after cleaning", num(c.clean_rows), pct((c.clean_rows / c.raw_rows) * 100, 1) + " of raw"),
      card("Dropped: bad price", num(c.dropped_null_or_zero_price + c.dropped_selling_gt_mrp), "null/zero or price > MRP"),
      card("Dropped: duplicates", num(c.dropped_duplicates)), card("Stale products", num(q.stale_products.length), "no snapshot in 48h"),
      card("Gaps > 48h", num(q.historic_gaps.length), "across the window"),
      card("MRP assumed", pct(q.share_mrp_assumed_equal_price * 100, 1), "snapshots with no struck-through price"),
    ].join("");
    const rows = [];
    for (const l of q.scrape_log_summary) rows.push({ k: "Scrape log", a: `${cap(l.platform)} · ${l.status}`, b: num(l.n) });
    rows.push({ k: "Review-count decreases smoothed", a: "carried forward", b: num(c.review_count_decreases_smoothed) });
    for (const s of q.stale_products.slice(0, 10)) rows.push({ k: "Stale product", a: (byId[s.product_id] || {}).name || s.product_id, b: "last seen " + s.last_seen.slice(0, 10) });
    for (const g of q.historic_gaps.slice(0, 10)) rows.push({ k: "Gap", a: (byId[g.product_id] || {}).name || g.product_id, b: `${num(g.gap_hours, 0)} h, ended ${g.ended_at.slice(0, 10)}` });
    table("t-quality", [{ h: "Type", f: (r) => r.k }, { h: "Detail", f: (r) => r.a }, { h: "Value", num: true, f: (r) => r.b }], rows, "Nothing to report.");
  }

  // ---------- plumbing
  const renderers = { overview: renderOverview, tracker: renderTracker, elasticity: renderElasticity, positioning: renderPositioning, method: renderMethod };
  let current = "overview";

  function show(view) {
    if (!D) return;
    if (!D.meta.has_data) view = "overview";
    if (!VIEWS.includes(view)) view = "overview";
    current = view;
    VIEWS.forEach((v) => { $(v).hidden = v !== view; });
    document.querySelectorAll("#nav a").forEach((a) => (a.getAttribute("href") === "#" + view ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
    $("filters").hidden = !D.meta.has_data || view === "overview" || view === "method";
    if (D.meta.has_data) renderers[view]();
  }

  function fillFilters() {
    const plats = [...new Set(D.products.map((p) => p.platform))].sort();
    const brands = [...new Set(D.products.map((p) => p.brand))].sort();
    $("f-platform").innerHTML = '<option value="">All</option>' + plats.map((p) => `<option value="${esc(p)}">${esc(cap(p))}</option>`).join("");
    $("f-brand").innerHTML = '<option value="">All</option>' + brands.map((b) => `<option value="${esc(b)}">${esc(b)}</option>`).join("");
    if (D.dates.length) { $("f-from").min = $("f-to").min = D.dates[0]; $("f-from").max = $("f-to").max = D.dates[D.dates.length - 1]; }
  }

  function bind() {
    window.addEventListener("hashchange", () => show(location.hash.slice(1)));
    for (const [id, key] of [["f-platform", "platform"], ["f-brand", "brand"], ["f-from", "from"], ["f-to", "to"]]) {
      $(id).addEventListener("change", (e) => { F[key] = e.target.value; show(current); });
    }
    $("f-reset").addEventListener("click", () => {
      Object.keys(F).forEach((k) => (F[k] = ""));
      ["f-platform", "f-brand", "f-from", "f-to"].forEach((id) => ($(id).value = ""));
      show(current);
    });
    $("product-select").addEventListener("change", drawPrice);
    $("theme").addEventListener("click", () => {
      const root = document.documentElement;
      const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("theme", root.dataset.theme); } catch (_) { /* storage unavailable */ }
      show(current);
    });
  }

  async function init() {
    try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch (_) { /* ignore */ }
    bind();
    try {
      const r = await fetch(DATA_URL, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      D = await r.json();
    } catch (err) {
      $("loading").hidden = true;
      const e = $("load-error"); e.hidden = false;
      e.textContent = `Could not load ${DATA_URL} (${err.message}). Run "python -m pricing.export" to generate it, or open this page through a web server rather than file://.`;
      return;
    }
    $("loading").hidden = true;
    if (DEMO) { $("synthetic-banner").hidden = false; $("leave-demo").href = location.pathname; }
    if (D.meta.synthetic && !DEMO) $("synthetic-banner").hidden = false;
    D.products = D.products || []; byId = Object.fromEntries(D.products.map((p) => [p.id, p]));
    if (D.meta.has_data) {
      fillFilters();
      $("foot-meta").textContent = `Data generated ${D.meta.generated_at.slice(0, 16).replace("T", " ")} UTC · ${D.meta.window_start.slice(0, 10)} → ${D.meta.window_end.slice(0, 10)}`;
    } else {
      $("empty-state").hidden = false;
      $("empty-state").innerHTML = `<strong>No data collected yet.</strong> ${esc(D.meta.note || "")} Once the daily scraper has run, this page fills in automatically. ` +
        `Want to see how it will look? <a href="?demo=1">Open the clearly-labelled synthetic demo</a>.`;
      $("cards").innerHTML = ""; $("findings").innerHTML = "<li>Waiting for the first scrape.</li>";
    }
    show(location.hash.slice(1) || "overview");
  }
  init();
})();
