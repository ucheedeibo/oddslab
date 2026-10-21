(function () {
  "use strict";

  // ---------- small helpers ----------
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (v, d = 0) => (typeof v === "number" && isFinite(v) ? v : d);
  const pc = (x) => `${Math.round(num(x) * 100)}%`;
  const localToday = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };
  const longDate = (iso) => {
    const d = new Date(`${iso}T12:00:00`);
    return isNaN(d) ? iso : d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  };
  const timeOf = (iso) => {
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage blocked or full */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };

  const TIERS = [["2.0", "Safe", "~2.00"], ["3.0", "Balanced", "~3.00"], ["5.0", "High value", "~5.00"]];
  const tierName = (t) => (TIERS.find((x) => x[0] === t) || TIERS[1])[1];

  const S = { screen: "home", back: "home", tier: "3.0", date: localToday(), matchId: null, why: null, shareSlip: null };
  const cache = { slips: {}, preds: {}, live: null };
  const reg = [];            // objects referenced from data-ref attributes in the current screen
  const TTL = 5 * 60 * 1000;

  // ---------- icons ----------
  const ICON = {
    home: '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/>',
    fixtures: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    slips: '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/>',
    today: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    account: '<circle cx="12" cy="8" r="4"/><path d="M4 21c1-4 4-6 8-6s7 2 8 6"/>',
  };
  const NAV = [["home", "Home"], ["fixtures", "Fixtures"], ["slips", "Slips"], ["today", "Today"], ["account", "Account"]];

  // ---------- network ----------
  async function api(path) {
    let token = store.get("app_api_token", "");
    if (!token) {
      token = prompt("Please enter your APP_API_TOKEN from Render:") || "";
      if (token) store.set("app_api_token", token.trim());
    }
    const res = await fetch(path, { headers: { "x-requested-with": "oddslab", Authorization: `Bearer ${token}` } });
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch (e) { throw new Error(`Server returned a non-JSON response (${res.status}).`); }
    if (res.status === 401) {
      store.del("app_api_token");
      throw new Error("Unauthorized. Enter your APP_API_TOKEN in the Account tab.");
    }
    if (!res.ok) throw new Error(data.detail || data.error || `Server error: ${res.status}`);
    return data;
  }

  const fresh = (entry) => entry && Date.now() - entry.t < TTL;

  async function loadSlips(force) {
    const key = `${S.date}|${S.tier}`;
    if (!force && fresh(cache.slips[key])) return;
    cache.slips[key] = { t: Date.now(), loading: true };
    render();
    try {
      cache.slips[key] = { t: Date.now(), data: await api(`/oa/slips?date=${encodeURIComponent(S.date)}&target_odds=${encodeURIComponent(S.tier)}`) };
      if (cache.slips[key].data.status === "loading") cache.slips[key].t = 0; // retry on next visit
    } catch (err) {
      cache.slips[key] = { t: 0, error: err.message };
    }
    render();
  }

  async function loadPreds(force) {
    if (!force && fresh(cache.preds[S.date])) return;
    const day = S.date;
    cache.preds[day] = { t: Date.now(), loading: true };
    render();
    try {
      cache.preds[day] = { t: Date.now(), data: await api(`/oa/predictions?date=${encodeURIComponent(day)}`) };
      if (cache.preds[day].data.status === "loading") cache.preds[day].t = 0;
    } catch (err) {
      cache.preds[day] = { t: 0, error: err.message };
    }
    render();
  }

  async function loadLive(force) {
    if (!force && fresh(cache.live)) return;
    cache.live = { t: Date.now(), loading: true };
    render();
    try {
      cache.live = { t: Date.now(), data: await api("/oa/live-fixtures") };
    } catch (err) {
      cache.live = { t: 0, error: err.message };
    }
    render();
  }

  // ---------- shared pieces ----------
  const card = (inner, cls = "") => `<div class="card ${cls}">${inner}</div>`;
  const loadingCard = (msg) => card(`<div class="mute" style="text-align:center">${esc(msg)}</div>`);
  const errorCard = (msg) => card(`<div class="err">${esc(msg)}</div>`);
  const notice = (icon, title, text, extra = "") =>
    card(`<div class="big">${icon}</div><b>${esc(title)}</b><div class="mute">${esc(text)}</div>${extra}`, "notice");

  function statusNotice(d) {
    if (!d) return "";
    if (d.status === "provider_error") return notice("⚠️", "Football data unavailable", d.message || "The data service could not be reached.");
    if (d.status === "no_fixtures") return notice("⚽", "No fixtures this day", d.message || "No matches are scheduled for this date.");
    if (d.status === "loading") return notice("⏳", "Loading match history", d.message || "Loading in the background. Try again in a minute or two.");
    if (d.status === "no_predictions") return notice("🔍", "Not enough data to predict yet", d.message || "Matches were found but could not be rated.");
    if (d.status === "no_slips") return notice("🎯", "No slip near this target", d.message || "Try another target.");
    return "";
  }

  const crest = (url) => (typeof url === "string" && url.startsWith("https://") ? `<img class="cr" src="${esc(url)}" alt="">` : "");
  const teamsInline = (h, hl, a, al) => `<span class="tm">${crest(hl)}${esc(h)}</span> <span class="mute">v</span> <span class="tm">${crest(al)}${esc(a)}</span>`;
  const confBadge = (c) => `<span class="badge ${c === "Medium" ? "good" : "warn"}">${esc(c || "Low")} data</span>`;
  const chance = (p) => (p >= 0.45 ? ["Solid", "good"] : p >= 0.30 ? ["Risky", "warn"] : ["Long shot", "bad"]);
  const ref = (obj) => { reg.push(obj); return reg.length - 1; };

  function dateRow() {
    return `<div class="row" style="margin-top:12px"><input type="date" id="day" value="${esc(S.date)}" aria-label="Match date" style="flex:1">
      <button class="btn" data-act="refresh" style="flex:none;padding:9px 14px">Refresh</button></div>`;
  }

  function tierChips() {
    return `<div class="chips">${TIERS.map(([v, n, t]) => `<button class="chip${v === S.tier ? " on" : ""}" data-act="tier" data-v="${v}">${n}<br><span class="sm">${t}</span></button>`).join("")}</div>`;
  }

  // Slip legs. Tap a leg to see why.
  function legRows(slip) {
    return (slip.legs || []).map((l) => {
      const hasWhy = !!l.why;
      const r = ref({ leg: l });
      const lift = l.base_rate ? `<div class="mute sm">Model ${pc(l.probability)} vs league ${pc(l.base_rate)}</div>` : "";
      return `<div class="leg${hasWhy ? " tap" : ""}" ${hasWhy ? `data-act="why" data-ref="${r}"` : ""}>
        <div><b>${esc(l.selection)}</b><span class="mute">${teamsInline(l.home_team, l.home_logo, l.away_team, l.away_logo)}</span>${lift}</div>
        <div class="r"><div class="num" style="font-size:18px">${num(l.odds).toFixed(2)}</div><div class="mute sm">${pc(l.probability)}</div></div></div>`;
    }).join("");
  }

  function ring(p) {
    const C = 226.2;
    return `<div style="position:relative;width:88px;height:88px;flex:none">
      <svg width="88" height="88" viewBox="0 0 88 88" role="img" aria-label="Slip chance">
        <circle cx="44" cy="44" r="36" fill="none" stroke="var(--line)" stroke-width="8"/>
        <circle id="ring" class="ring" data-offset="${(C * (1 - Math.min(Math.max(p, 0), 1))).toFixed(1)}" cx="44" cy="44" r="36" fill="none" stroke="var(--acc)" stroke-width="8" stroke-linecap="round" stroke-dasharray="${C}" stroke-dashoffset="${C}" transform="rotate(-90 44 44)"/>
      </svg>
      <div class="num" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:22px">${pc(p)}</div></div>`;
  }

  function slipText(slip, tier) {
    const lines = [`🎯 *OddsLab ${tierName(tier)} Pick* (Fair Odds: ${num(slip.combined_odds).toFixed(2)})`, ""];
    (slip.legs || []).forEach((l) => lines.push(`• ${l.home_team} vs ${l.away_team}`, `  👉 Selection: ${l.selection} @ ${num(l.odds).toFixed(2)}`));
    lines.push("", `Model chance: ${pc(slip.probability)}`, "Generated with OddsLab. Model odds, not advice.");
    return lines.join("\n");
  }

  // ---------- your prices (kept on this phone, with a timestamp) ----------
  const PKEY = "oddslab_prices";
  const prices = () => { try { return JSON.parse(store.get(PKEY, "{}")); } catch (e) { return {}; } };
  const savePrice = (id, odds) => {
    const all = prices();
    if (odds) all[id] = { odds, t: new Date().toISOString() }; else delete all[id];
    store.set(PKEY, JSON.stringify(all));
  };
  // EV = probability x odds - 1. Stake = quarter Kelly, hidden when data confidence is Low.
  function evHtml(p, odds, conf, t) {
    if (!(odds > 1) || !(p > 0)) return "";
    const ev = p * odds - 1;
    let txt = `EV ${ev >= 0 ? "+" : ""}${(ev * 100).toFixed(1)}%`;
    if (ev > 0 && conf !== "Low") txt += ` · stake ${((ev / (odds - 1)) / 4 * 100).toFixed(1)}% of bankroll`;
    else if (ev > 0) txt += " · stake hidden (low data)";
    if (t) txt += ` · saved ${new Date(t).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`;
    return `<span class="${ev >= 0 ? "pos" : "neg"}">${esc(txt)}</span>`;
  }
  function priceInput(id, p, conf, placeholder) {
    const saved = prices()[id];
    return `<input class="odds-input" inputmode="decimal" placeholder="${esc(placeholder || "odds")}" value="${saved ? esc(saved.odds) : ""}" data-id="${esc(id)}" data-p="${esc(p)}" data-conf="${esc(conf || "")}">`;
  }
  const evOut = (id, p, conf) => { const s = prices()[id]; return s ? evHtml(p, s.odds, conf, s.t) : ""; };

  // ---------- screens ----------
  function homeScreen() {
    const key = `${S.date}|${S.tier}`;
    const e = cache.slips[key];
    let body;
    if (!e || e.loading) body = loadingCard("Building today's pick...");
    else if (e.error) body = errorCard(e.error);
    else {
      const d = e.data, slip = (d.slips || [])[0];
      if (!slip) body = statusNotice(d) || notice("🔍", "No qualified slips", "Not enough qualifying matches for this date.");
      else {
        const c = chance(slip.probability);
        body = card(`
          <div class="row sp"><span class="mute">Pick of the day · ${esc(tierName(S.tier))}</span><span class="badge ${c[1]}">${c[0]}</span></div>
          <div class="row" style="gap:16px;margin:12px 0 4px">${ring(slip.probability)}
            <div><div class="mute">Fair odds</div><div class="num" style="font-size:38px;line-height:1.05">${num(slip.combined_odds).toFixed(2)}</div>
            <div class="mute sm">${(slip.legs || []).length}-leg slip · model chance ${pc(slip.probability)}</div></div></div>
          ${legRows(slip)}
          <div class="mute sm" style="margin-top:8px">Tap a pick to see why. Fair odds are the model's, not a bookmaker's price.</div>
          <div class="row" style="margin-top:10px"><button class="btn" data-act="share" data-ref="${ref({ slip })}">Share pick</button>
          <button class="btn" data-act="nav" data-v="slips">All ${esc(tierName(S.tier))} slips</button></div>`);
        if (d.message) body = `<div class="mute sm" style="margin-top:10px;color:var(--warn)">${esc(d.message)}</div>` + body;
      }
      body += `<div class="g2"><div class="stat"><div class="mute">Matches rated</div><div class="num">${num(d.games_considered)} of ${num(d.fixtures_total)}</div></div>
        <div class="stat"><div class="mute">Not rated</div><div class="num">${(d.skipped || []).length}</div></div></div>`;
    }
    return `<div class="top"><h1>OddsLab</h1><span class="mute sm">Think beyond the score</span></div>${dateRow()}${tierChips()}${body}`;
  }

  function fixtureCard(f, rated) {
    const has = f.home_score != null && f.away_score != null;
    const p = f.prediction;
    const right = has ? `<span class="badge good">${esc(f.home_score)} - ${esc(f.away_score)}</span>` : rated ? confBadge(f.confidence) : '<span class="badge">Preview only</span>';
    const lean = rated && p ? `<div class="mute sm">Model lean: ${esc(p.selection)} · ${esc(p.probability_percent)}%</div>` : (f.reason ? `<div class="mute sm">${esc(f.reason)}</div>` : "");
    return `<div class="card${rated ? " tap" : ""}" ${rated ? `data-act="match" data-id="${esc(f.fixture_id)}"` : ""}>
      <div class="row sp"><span class="mute sm">${esc(f.league || "")}${f.kickoff ? " · " + esc(timeOf(f.kickoff)) : ""}</span>${right}</div>
      <div class="teams"><span class="tm">${crest(f.home_logo)}${esc(f.home_team)}</span><span class="tm">${crest(f.away_logo)}${esc(f.away_team)}</span></div>${lean}</div>`;
  }

  function fixturesScreen() {
    const e = cache.preds[S.date];
    let body;
    if (!e || e.loading) body = loadingCard("Rating every match...");
    else if (e.error) body = errorCard(e.error);
    else {
      const d = e.data, rated = d.predictions || [], skipped = d.skipped || [];
      if (!rated.length && !skipped.length) body = statusNotice(d) || notice("⚽", "No fixtures", "No matches this day.");
      else {
        body = `<div class="mute" style="margin-top:6px">${esc(longDate(S.date))} · ${rated.length} rated, ${skipped.length} not rated</div>`;
        if (d.status === "loading" || d.status === "provider_error") body += statusNotice(d);
        body += rated.map((f) => fixtureCard(f, true)).join("") + skipped.map((f) => fixtureCard(f, false)).join("");
        if ((d.notes || []).length) body += `<details style="margin-top:12px"><summary>${d.notes.length} note(s)</summary>${d.notes.map((n) => `<p class="mute sm">${esc(n)}</p>`).join("")}</details>`;
      }
    }
    return `<h1>Fixtures</h1>${dateRow()}${body}`;
  }

  function matchScreen() {
    const d = (cache.preds[S.date] || {}).data;
    const f = d && (d.predictions || []).find((x) => String(x.fixture_id) === String(S.matchId));
    if (!f) return `<button class="back" data-act="nav" data-v="fixtures">‹ Fixtures</button>${notice("🔍", "Match not found", "Go back and reload the fixtures.")}`;
    const p = f.prediction, top = (p.top_scores || []).map((s) => `${esc(s.score)} (${esc(s.prob)}%)`).join(" · ");
    const rows = [["home", "Home win", p.p_home], ["draw", "Draw", p.p_draw], ["away", "Away win", p.p_away], ["over25", "Over 2.5", p.p_over_2_5], ["btts", "Both teams score", p.p_btts]].map(([m, label, pct]) => {
      const id = `${f.fixture_id}:${m}`, prob = num(pct) / 100;
      return `<div class="mk"><div><div style="font-size:14px">${label}</div><div class="mute sm">${esc(pct)}% · fair ${(1 / Math.max(prob, 0.001)).toFixed(2)}</div></div>
        <div class="sm ev-out">${evOut(id, prob, f.confidence)}</div>${priceInput(id, prob, f.confidence)}</div>`;
    }).join("");
    const whyBtn = f.why ? `<div class="card tap" data-act="why" data-ref="${ref({ match: f })}"><div class="row sp"><div><div class="mute sm">Why this pick</div><div style="font-size:14px">${esc(f.why.verdict)}</div></div><span class="mute">›</span></div></div>` : "";
    return `<button class="back" data-act="nav" data-v="${esc(S.back === "match" ? "fixtures" : S.back)}">‹ Back</button>
      <div class="mute sm">${esc(f.league)} · ${esc(timeOf(f.kickoff))}</div>
      <h2>${teamsInline(f.home_team, f.home_logo, f.away_team, f.away_logo)}</h2>
      ${card(`<div class="row sp"><span class="mute">Expected goals (model)</span>${confBadge(f.confidence)}</div>
        <div class="row sp" style="margin-top:6px"><span class="num" style="font-size:32px">${num(p.xG_home).toFixed(2)}</span><span class="num" style="font-size:32px">${num(p.xG_away).toFixed(2)}</span></div>
        <div class="bar"><div style="width:${num(p.p_home)}%;background:var(--acc)"></div><div style="width:${num(p.p_draw)}%;background:var(--mute)"></div><div style="width:${num(p.p_away)}%;background:var(--warn)"></div></div>
        <div class="row sp mute sm"><span>Home ${Math.round(num(p.p_home))}%</span><span>Draw ${Math.round(num(p.p_draw))}%</span><span>Away ${Math.round(num(p.p_away))}%</span></div>
        <div class="mute sm" style="margin-top:10px">Likely scorelines: ${top}</div>
        <div class="mute sm" style="margin-top:4px">Model lean: ${esc(p.selection)} (${esc(p.probability_percent)}%)</div>`)}
      ${whyBtn}
      ${card(`<div class="row sp"><b style="font-weight:500">Your prices</b><span class="mute sm">Odds from your bookmaker</span></div>${rows}
        <div class="mute sm" style="margin-top:8px">Model probabilities carry error, so keep stakes small.</div>`)}`;
  }

  function whyScreen() {
    const w = S.why;
    if (!w) return `<button class="back" data-act="nav" data-v="home">‹ Home</button>${notice("🔍", "Nothing to show", "Open a pick first.")}`;
    const tg = { supports: "good", caution: "warn", neutral: "" }, tl = { supports: "Supports", caution: "Caution", neutral: "Neutral" };
    const s = w.why.summary || {};
    const rows = (w.why.rows || []).map((r) => `<div class="leg" style="align-items:flex-start">
      <span class="badge ${tg[r.tag] || ""}" style="min-width:74px;text-align:center">${tl[r.tag] || "Neutral"}</span>
      <div style="flex:1;min-width:0"><b>${esc(r.title)}</b><span class="mute">${esc(r.text)}</span></div>
      <span class="chipn${r.thin ? " thin" : ""}">${esc(r.sample)}${r.thin ? " · thin" : ""}</span></div>`).join("");
    const bars = (w.why.samples || []).map((t) => `<div class="row" style="margin-top:8px"><span class="mute" style="width:96px">${esc(t.name)}</span>
      <div class="conf"><div style="width:${Math.min(100, num(t.n) * 10)}%"></div></div><span class="mute sm">${esc(t.n)}/10</span></div>`).join("");
    return `<button class="back" data-act="nav" data-v="${esc(S.back)}">‹ Back</button><div class="mute sm">Why this pick</div>
      <h2>${teamsInline(w.home, w.homeLogo, w.away, w.awayLogo)}</h2>
      ${card(`<div><div class="mute sm">Pick</div><b style="font-weight:500">${esc(w.pick)}</b></div><div class="row sp" style="margin-top:6px"><span class="num" style="font-size:26px">${pc(w.why.model_p)}</span><span class="mute">fair odds ${num(w.why.fair_odds).toFixed(2)}</span></div>`, "")}
      ${card(`<div style="font-size:14px">${esc(w.why.verdict)}</div>`)}
      <div class="row" style="margin-top:12px;gap:6px"><span class="badge good">${num(s.supports)} support</span><span class="badge warn">${num(s.caution)} caution</span><span class="badge">${num(s.neutral)} neutral</span></div>
      ${card(rows, "")}
      ${card(`<div class="row sp"><span class="mute">Data confidence</span>${confBadge(w.why.confidence)}</div>${bars}<div class="mute sm" style="margin-top:8px">Counted from each team's last 10 matches. Under 8 is a thin sample.</div>`)}`;
  }

  function slipsScreen() {
    const key = `${S.date}|${S.tier}`, e = cache.slips[key];
    let body;
    if (!e || e.loading) body = loadingCard("Building slips...");
    else if (e.error) body = errorCard(e.error);
    else {
      const slips = e.data.slips || [];
      body = slips.length ? slips.map((slip, i) => {
        const c = chance(slip.probability), id = `slip:${S.date}:${S.tier}:${slip.legs.map((l) => `${l.fixture_id}-${l.selection}`).join("+")}`;
        return card(`<div class="row sp"><b class="num" style="font-size:18px">Slip ${i + 1} · ${num(slip.combined_odds).toFixed(2)}</b><span class="badge ${c[1]}">${c[0]} · ${pc(slip.probability)}</span></div>
          <div class="mute sm" style="margin-top:2px">${esc(slip.note)}</div>${legRows(slip)}
          <div class="mk" style="grid-template-columns:minmax(0,1fr) 84px"><div><div style="font-size:14px">Your price for this slip</div><div class="sm ev-out">${evOut(id, slip.probability, "Medium")}</div></div>${priceInput(id, slip.probability, "Medium", "price")}</div>
          <div class="row" style="margin-top:8px"><button class="btn" data-act="share" data-ref="${ref({ slip })}">Share</button><button class="btn" data-act="copy" data-ref="${ref({ slip })}">Copy text</button></div>`);
      }).join("") : (statusNotice(e.data) || notice("🔍", "No qualified slips", "Not enough qualifying matches for this date."));
      if (e.data.message && slips.length) body = `<div class="mute sm" style="margin-top:10px;color:var(--warn)">${esc(e.data.message)}</div>` + body;
      if (e.data.disclaimer) body += `<div class="mute sm" style="margin:6px 0 16px">${esc(e.data.disclaimer)}</div>`;
    }
    return `<h1>Slips</h1>${dateRow()}${tierChips()}${body}`;
  }

  function shareScreen() {
    const slip = S.shareSlip;
    if (!slip) return `<button class="back" data-act="nav" data-v="home">‹ Home</button>${notice("🎯", "Nothing to share", "Open a slip first.")}`;
    const legs = (slip.legs || []).map((l) => `<div class="leg"><div><b>${esc(l.selection)}</b><span class="mute">${teamsInline(l.home_team, l.home_logo, l.away_team, l.away_logo)}</span></div></div>`).join("");
    return `<button class="back" data-act="nav" data-v="${esc(S.back)}">‹ Back</button>
      <div class="share"><div class="row sp"><b class="num" style="font-size:18px">OddsLab</b><span class="mute">${esc(longDate(S.date))}</span></div>
        <div class="row" style="margin-top:12px"><span class="mute">Pick</span><span class="badge good">${esc(tierName(S.tier))}</span></div>
        <div class="row" style="align-items:baseline"><span class="num" style="font-size:54px;line-height:1.1">${num(slip.combined_odds).toFixed(2)}</span><span class="mute">fair odds</span></div>
        <div class="row sp mute sm" style="margin-top:6px"><span>Model chance</span><b style="color:var(--ink)">${pc(slip.probability)}</b></div>
        <div style="height:8px;background:var(--line);border-radius:4px;margin-top:4px"><div style="height:100%;width:${Math.round(num(slip.probability) * 100)}%;background:var(--acc);border-radius:4px"></div></div>
        ${legs}<div class="row sp mute sm" style="margin-top:10px"><span>Who’s with me?</span><span>Model odds, not advice</span></div></div>
      <div class="row" style="margin-top:12px"><button class="btn pri" data-act="copy" data-ref="${ref({ slip })}">Copy text</button></div><div id="cp" class="mute sm" style="margin-top:8px;min-height:18px"></div>`;
  }

  function todayScreen() {
    const e = cache.live;
    let body;
    if (!e || e.loading) body = loadingCard("Fetching today's matches...");
    else if (e.error) body = errorCard(e.error);
    else {
      const list = e.data.fixtures || [];
      body = list.length ? list.map((f) => fixtureCard(f, false).replace('<span class="badge">Preview only</span>', `<span class="badge">${esc(f.status || "")}</span>`)).join("") : (statusNotice(e.data) || notice("⚽", "No matches today", "Nothing scheduled in the supported competitions."));
    }
    return `<h1>Today</h1><div class="mute">Scores can be delayed on the free data plan.</div><div class="row" style="margin-top:12px"><button class="btn" data-act="refresh">Refresh</button></div>${body}`;
  }

  function accountScreen() {
    const saved = !!store.get("app_api_token", "");
    const theme = store.get("oddslab_theme", "auto");
    const seg = (v, label) => `<button class="chip${theme === v ? " on" : ""}" data-act="theme" data-v="${v}">${label}</button>`;
    return `<h1>Account</h1>
      ${card(`<label class="mute" for="tok">APP_API_TOKEN</label><input id="tok" type="password" placeholder="Enter your token" style="margin-top:6px">
        <div class="row" style="margin-top:10px"><button class="btn pri" data-act="savetoken">Save</button><button class="btn" data-act="cleartoken">Clear</button></div>
        <div id="tokmsg" class="mute sm" style="margin-top:8px">${saved ? "✅ Token saved on this phone." : "⚠️ No token set."}</div>`)}
      ${card(`<b style="font-weight:500">Theme</b><div class="seg">${seg("auto", "Auto")}${seg("light", "Light")}${seg("dark", "Dark")}</div>`)}
      ${card(`<b style="font-weight:500">Tools</b><div class="row" style="margin-top:8px"><button class="btn" data-act="accuracy">Model accuracy check</button><button class="btn" data-act="perf">30-day performance</button></div>
        <div id="tools-out"></div>`)}`;
  }

  // ---------- tools (accuracy and performance) ----------
  async function runAccuracy() {
    const out = $("tools-out");
    if (!out) return;
    out.innerHTML = loadingCard("Replaying recent matches (up to a minute)...");
    try {
      let html = "";
      for (const [label, url] of [["Premier League", "/oa/backtest?league=2021"], ["Champions League (pooled)", "/oa/backtest?league=2001&pooled=true"], ["Champions League (single)", "/oa/backtest?league=2001"]]) {
        const d = await api(url);
        if (d.status !== "ok") { html += notice("⏳", label, d.message || "Not available yet."); continue; }
        const mk = Object.values(d.markets || {}).map((m) => `<div class="sm" style="margin-top:8px">${esc(m.label)}: ${m.better_than_baseline ? "✅" : "⚠️"} Brier ${esc(m.model.brier)} vs baseline ${esc(m.baseline.brier)} (${esc(m.matches_scored)} matches)
          <div class="mute sm">${(m.calibration || []).map((c) => `Predicted ${esc(c.predicted)} → actual ${esc(c.actual)} (${esc(c.matches)})`).join("<br>")}</div></div>`).join("");
        html += card(`<b style="font-weight:500">${esc(label)}</b><div class="sm">${d.better_than_baseline ? "✅ Beats the naive baseline" : "⚠️ Does NOT beat the naive baseline"}</div>
          <div class="mute sm" style="margin-top:6px">Matches scored: ${esc(d.matches_scored)} · top pick correct: ${esc(d.top_pick_accuracy)}</div>
          <div class="mute sm">Model RPS ${esc(d.model.rps)} · log-loss ${esc(d.model.log_loss)} · baseline RPS ${esc(d.baseline.rps)}</div>
          <div class="mute sm" style="margin-top:6px">${(d.calibration || []).map((c) => `Predicted ${esc(c.predicted)} → actual ${esc(c.actual)} (${esc(c.matches)})`).join("<br>")}</div>${mk}
          <div class="mute sm" style="margin-top:8px">${esc(d.how_to_read)}</div>`);
      }
      out.innerHTML = html;
    } catch (err) { out.innerHTML = errorCard(err.message); }
  }

  async function runPerformance() {
    const out = $("tools-out");
    if (!out) return;
    out.innerHTML = loadingCard("Loading performance...");
    try {
      const d = await api("/oa/performance?days=30");
      if (!d.total_slips) { out.innerHTML = notice("📊", "No settled slips yet", "Appears once past slips have finished."); return; }
      const box = (l, v, cls = "") => `<div class="stat ${cls}"><div class="mute">${l}</div><div class="num" style="font-size:20px">${esc(v)}</div></div>`;
      out.innerHTML = `<div class="g2">${box("Total slips", `${d.total_slips} (${d.won_slips} won)`)}${box("Win rate", `${d.win_rate_percent}%`)}
        ${box("Net profit", `${d.net_profit > 0 ? "+" : ""}${d.net_profit}`, d.net_profit >= 0 ? "good" : "bad")}${box("ROI", `${d.roi_percent > 0 ? "+" : ""}${d.roi_percent}%`, d.roi_percent >= 0 ? "good" : "bad")}</div>`;
    } catch (err) { out.innerHTML = errorCard(err.message); }
  }

  // ---------- render ----------
  function render() {
    reg.length = 0;
    const screens = { home: homeScreen, fixtures: fixturesScreen, match: matchScreen, slips: slipsScreen, today: todayScreen, account: accountScreen, why: whyScreen, share: shareScreen };
    const app = $("app");
    if (!app) return;
    app.innerHTML = (screens[S.screen] || homeScreen)();
    const act = { match: "fixtures", why: S.back === "match" ? "fixtures" : S.back, share: S.back }[S.screen] || S.screen;
    $("nav").innerHTML = NAV.map(([k, label]) => `<button class="${k === act ? "on" : ""}" data-act="nav" data-v="${k}"><svg viewBox="0 0 24 24" aria-hidden="true">${ICON[k]}</svg>${label}</button>`).join("");
    const r = $("ring");
    if (r) setTimeout(() => { const el = $("ring"); if (el) el.style.strokeDashoffset = el.getAttribute("data-offset"); }, 40);
  }

  function go(screen, opts = {}) {
    if (opts.back) S.back = opts.back;
    S.screen = screen;
    if (typeof window !== "undefined" && window.scrollTo) window.scrollTo(0, 0);
    if (screen === "home") loadSlips(false);
    else if (screen === "slips") loadSlips(false);
    else if (screen === "fixtures") loadPreds(false);
    else if (screen === "today") loadLive(false);
    else if (screen === "match") loadPreds(false);
    render();
  }

  function applyTheme(t) {
    const root = document.documentElement;
    if (t === "light" || t === "dark") root.setAttribute("data-theme", t); else root.removeAttribute("data-theme");
  }

  async function copyText(slip, btn) {
    const text = slipText(slip, S.tier);
    try {
      await navigator.clipboard.writeText(text);
      const cp = $("cp");
      if (cp) cp.textContent = "✅ Copied. Paste it into WhatsApp.";
      if (btn) { const o = btn.textContent; btn.textContent = "✅ Copied"; setTimeout(() => { btn.textContent = o; }, 1800); }
    } catch (err) { alert("Could not copy: " + err); }
  }

  // ---------- events ----------
  document.addEventListener("click", (e) => {
    const el = e.target.closest && e.target.closest("[data-act]");
    if (!el) return;
    const act = el.getAttribute("data-act"), v = el.getAttribute("data-v"), r = reg[Number(el.getAttribute("data-ref"))];
    if (act === "nav") go(v);
    else if (act === "tier") { S.tier = v; go(S.screen === "slips" ? "slips" : "home"); }
    else if (act === "refresh") {
      if (S.screen === "today") loadLive(true);
      else if (S.screen === "fixtures") loadPreds(true);
      else if (S.screen === "slips") loadSlips(true);
      else { loadSlips(true); }
    }
    else if (act === "match") { S.matchId = el.getAttribute("data-id"); go("match", { back: "fixtures" }); }
    else if (act === "why" && r) {
      const from = S.screen === "match" ? "match" : S.screen;
      if (r.leg) S.why = { why: r.leg.why, pick: r.leg.selection, home: r.leg.home_team, away: r.leg.away_team, homeLogo: r.leg.home_logo, awayLogo: r.leg.away_logo };
      else S.why = { why: r.match.why, pick: r.match.prediction.selection, home: r.match.home_team, away: r.match.away_team, homeLogo: r.match.home_logo, awayLogo: r.match.away_logo };
      go("why", { back: from });
    }
    else if (act === "share" && r) { S.shareSlip = r.slip; go("share", { back: S.screen }); }
    else if (act === "copy" && r) copyText(r.slip, el);
    else if (act === "theme") { store.set("oddslab_theme", v); applyTheme(v); render(); }
    else if (act === "savetoken") {
      const t = $("tok"), m = $("tokmsg");
      if (t && t.value.trim()) { store.set("app_api_token", t.value.trim()); if (m) m.textContent = "✅ Token saved on this phone."; }
    }
    else if (act === "cleartoken") { store.del("app_api_token"); const m = $("tokmsg"); if (m) m.textContent = "🗑️ Token cleared."; }
    else if (act === "accuracy") runAccuracy();
    else if (act === "perf") runPerformance();
  });

  document.addEventListener("change", (e) => {
    if (e.target && e.target.id === "day" && e.target.value) {
      S.date = e.target.value;
      if (S.screen === "fixtures") loadPreds(false);
      else loadSlips(false);
      render();
    }
  });

  document.addEventListener("input", (e) => {
    const el = e.target.closest && e.target.closest(".odds-input");
    if (!el) return;
    const odds = parseFloat(String(el.value).replace(",", ".")), valid = odds > 1;
    savePrice(el.dataset.id, valid ? odds : null);
    const out = el.parentElement.querySelector(".ev-out") || el.parentElement.parentElement.querySelector(".ev-out");
    if (out) out.innerHTML = valid ? evHtml(parseFloat(el.dataset.p), odds, el.dataset.conf, new Date().toISOString()) : "";
  });

  // ---------- start ----------
  document.addEventListener("DOMContentLoaded", () => {
    applyTheme(store.get("oddslab_theme", "auto"));
    S.date = localToday();
    go("home");
  });
})();
