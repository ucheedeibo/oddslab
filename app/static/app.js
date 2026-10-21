document.addEventListener("DOMContentLoaded", () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const localToday = () => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };

  const dayInput = $("day");
  const today = localToday();
  if (dayInput) dayInput.value = today;
  const currentDate = () => (dayInput && dayInput.value) || today;

  let selectedTargetOdds = "3.0";

  // ---------- helpers ----------
  async function api(path) {
    let token = localStorage.getItem("app_api_token");
    if (!token) {
      token = prompt("Please enter your APP_API_TOKEN from Render:");
      if (token) localStorage.setItem("app_api_token", token.trim());
    }
    const res = await fetch(path, {
      headers: { "x-requested-with": "oddslab", Authorization: `Bearer ${token || ""}` },
    });
    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (e) {
      throw new Error(`Server returned a non-JSON response (${res.status}).`);
    }
    if (res.status === 401) {
      localStorage.removeItem("app_api_token");
      throw new Error("Unauthorized. Check your APP_API_TOKEN in the Account tab.");
    }
    if (!res.ok) throw new Error(data.detail || data.error || `Server error: ${res.status}`);
    return data;
  }

  const card = (inner, extra = "") => `<div class="card" style="${extra}">${inner}</div>`;
  const errorCard = (msg) => card(`<p style="color:#ef4444;font-size:0.85rem;margin:0;">${esc(msg)}</p>`);
  const loadingCard = (msg) => card(`<p style="color:#94a3b8;text-align:center;margin:0;">${esc(msg)}</p>`);

  const notice = (icon, title, text, extra = "") =>
    card(
      `<div style="font-size:1.8rem;margin-bottom:8px;">${icon}</div>
       <div style="font-size:1rem;font-weight:700;color:#f8fafc;margin-bottom:6px;">${esc(title)}</div>
       <p style="font-size:0.85rem;color:#94a3b8;margin:0;line-height:1.4;">${esc(text)}</p>${extra}`,
      "text-align:center;padding:20px 16px;"
    );

  // Turns the server's status/message into a friendly card (or "" when all is fine).
  function statusNotice(data) {
    if (data.status === "provider_error")
      return notice("⚠️", "Football data unavailable", data.message || "The data service could not be reached. Try again soon.");
    if (data.status === "no_fixtures")
      return notice("⚽", "No Fixtures Available", data.message || "No matches are scheduled for this date.");
    if (data.status === "loading")
      return notice("⏳", "Loading match history", data.message || "Loading data in the background. Try again in a minute or two.");
    if (data.status === "no_slips")
      return notice("🎯", "No slip near this target", data.message || "No combination of today's matches gets near this target.");
    if (data.status === "no_predictions")
      return notice("🔍", "Not enough data to predict yet", data.message || "Matches were found but could not be modelled.");
    return "";
  }

  const crest = (url) =>
    typeof url === "string" && url.startsWith("https://")
      ? `<img src="${esc(url)}" alt="" width="20" height="20" loading="lazy" style="object-fit:contain;vertical-align:middle;margin-right:6px;">`
      : "";

  // ---------- your bookmaker prices (kept on this phone, with a timestamp) ----------
  const ODDS_KEY = "oddslab_prices";
  const loadPrices = () => { try { return JSON.parse(localStorage.getItem(ODDS_KEY) || "{}"); } catch (e) { return {}; } };
  const savePrice = (id, odds) => {
    const all = loadPrices();
    if (odds) all[id] = { odds, t: new Date().toISOString() }; else delete all[id];
    try { localStorage.setItem(ODDS_KEY, JSON.stringify(all)); } catch (e) { /* storage may be full or blocked */ }
  };

  // EV = probability x odds - 1. Stake = quarter Kelly, hidden when data confidence is Low.
  function evHtml(p, odds, conf, t) {
    if (!(odds > 1) || !(p > 0)) return "";
    const ev = p * odds - 1;
    const color = ev >= 0 ? "#00E676" : "#ef4444";
    let txt = `EV ${ev >= 0 ? "+" : ""}${(ev * 100).toFixed(1)}%`;
    if (ev > 0 && conf !== "Low") txt += ` · stake ${((ev / (odds - 1)) / 4 * 100).toFixed(1)}% of bankroll`;
    else if (ev > 0) txt += " · stake hidden (low confidence)";
    if (t) txt += ` · saved ${new Date(t).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`;
    return `<span style="color:${color};">${esc(txt)}</span>`;
  }

  function oddsPanel(f) {
    const p = f.prediction || {};
    const prices = loadPrices();
    const items = [["home", "Home win", p.p_home], ["draw", "Draw", p.p_draw], ["away", "Away win", p.p_away],
                   ["over25", "Over 2.5", p.p_over_2_5], ["btts", "BTTS yes", p.p_btts]];
    const rows = items.map(([m, label, pct]) => {
      const id = `${f.fixture_id}:${m}`;
      const saved = prices[id];
      return `<div style="display:flex;align-items:center;gap:8px;margin-top:6px;font-size:0.8rem;">
        <span style="width:70px;color:#e2e8f0;">${label}</span>
        <span style="width:42px;color:#94a3b8;">${esc(pct)}%</span>
        <input class="odds-input" inputmode="decimal" placeholder="odds" value="${saved ? esc(saved.odds) : ""}"
          data-id="${esc(id)}" data-p="${esc((pct || 0) / 100)}" data-conf="${esc(f.confidence || "")}"
          style="width:64px;padding:5px;background:#0f172a;border:1px solid #334155;color:#fff;border-radius:6px;">
        <span class="ev-out" style="flex:1;">${saved ? evHtml((pct || 0) / 100, saved.odds, f.confidence, saved.t) : ""}</span>
      </div>`;
    }).join("");
    return `<details style="margin-top:8px;font-size:0.8rem;color:#94a3b8;"><summary>Enter bookmaker odds</summary>${rows}
      <p style="font-size:0.72rem;color:#64748b;margin:8px 0 0;">Book odds are the prices you type in. Model probabilities carry error, so keep stakes small.</p></details>`;
  }

  function whyHtml(w) {
    if (!w) return "";
    const icon = { supports: "✅", caution: "⚠️", neutral: "➖" };
    const rows = (w.rows || []).map((r) => `
      <div style="margin-top:6px;font-size:0.78rem;color:#cbd5e1;line-height:1.35;">
        ${icon[r.tag] || "➖"} <strong>${esc(r.title)}</strong> — ${esc(r.text)}
        <span style="font-size:0.68rem;padding:1px 6px;border-radius:8px;border:1px solid ${r.thin ? "#f59e0b" : "#334155"};color:${r.thin ? "#f59e0b" : "#94a3b8"};">${esc(r.sample)}${r.thin ? " · thin" : ""}</span>
      </div>`).join("");
    return `<details style="margin-top:8px;font-size:0.8rem;color:#94a3b8;"><summary>Why this pick</summary>
      <p style="font-size:0.8rem;color:#e2e8f0;margin:6px 0 0;">${esc(w.verdict)}</p>${rows}</details>`;
  }

  const statusLabel = (s) =>
    ({ TIMED: "Scheduled", SCHEDULED: "Scheduled", IN_PLAY: "LIVE", PAUSED: "Half-time", FINISHED: "Full-time",
       POSTPONED: "Postponed", CANCELLED: "Cancelled", SUSPENDED: "Suspended" }[s] || s || "Scheduled");

  function fixtureCard(f) {
    const has = f.home_score != null && f.away_score != null;
    const score = has ? `${f.home_score} - ${f.away_score}` : "VS";
    return `
      <div class="card" style="margin-bottom:10px;padding:12px;">
        <div style="font-size:0.75rem;color:#94a3b8;margin-bottom:6px;display:flex;justify-content:space-between;">
          <span>${esc(f.league || "League")}</span>
          <span style="color:#00E676;font-weight:600;">${esc(statusLabel(f.status))}</span>
        </div>
        <div style="font-weight:600;font-size:0.95rem;color:#FFFFFF;display:flex;justify-content:space-between;align-items:center;gap:8px;">
          <span>${crest(f.home_logo)}${esc(f.home_team)}<br>${crest(f.away_logo)}${esc(f.away_team)}</span>
          <span style="background:rgba(0,230,118,0.1);color:#00E676;padding:3px 8px;border-radius:6px;font-size:0.85rem;font-weight:700;border:1px solid rgba(0,230,118,0.2);white-space:nowrap;">${esc(score)}</span>
        </div>
      </div>`;
  }

  function fixturesHtml(title, data) {
    const head = `<div style="font-size:1rem;font-weight:700;margin-bottom:12px;color:#f8fafc;">${esc(title)}</div>`;
    const list = Array.isArray(data.fixtures) ? data.fixtures : [];
    if (list.length) return head + list.map(fixtureCard).join("");
    return head + (statusNotice(data) || notice("⚽", "No Fixtures Found", "No matches scheduled for this date."));
  }

  function predictionCard(f) {
    const p = f.prediction || {};
    const scores = (p.top_scores || []).map((s) => `${esc(s.score)} (${esc(s.prob)}%)`).join(" · ");
    const conf = f.confidence || "";
    const confColor = conf === "Medium" ? "#f59e0b" : "#ef4444";
    return `
      <div class="card" style="margin-bottom:10px;padding:12px;">
        <div style="font-size:0.75rem;color:#94a3b8;margin-bottom:6px;display:flex;justify-content:space-between;">
          <span>${esc(f.league || "League")}</span>
          <span style="color:${confColor};font-weight:600;">${esc(conf)} confidence</span>
        </div>
        <div style="font-weight:600;font-size:0.95rem;color:#FFFFFF;">${crest(f.home_logo)}${esc(f.home_team)} vs ${crest(f.away_logo)}${esc(f.away_team)}</div>
        <div style="margin-top:8px;font-size:0.85rem;color:#e2e8f0;">
          Home <strong>${esc(p.p_home)}%</strong> · Draw <strong>${esc(p.p_draw)}%</strong> · Away <strong>${esc(p.p_away)}%</strong>
        </div>
        <div style="margin-top:4px;font-size:0.8rem;color:#94a3b8;">
          Over 2.5: ${esc(p.p_over_2_5)}% · Both score: ${esc(p.p_btts)}%
        </div>
        <div style="margin-top:6px;font-size:0.85rem;color:#94a3b8;">
          Model lean: <strong style="color:#00E676;">${esc(p.selection)}</strong> (${esc(p.probability_percent)}%)
        </div>
        <div style="font-size:0.78rem;color:#94a3b8;margin-top:4px;">xG ${esc(p.xG_home)} - ${esc(p.xG_away)} · Likely: ${scores}</div>
        <p style="font-size:0.78rem;color:#94a3b8;margin:6px 0 0 0;line-height:1.4;">${esc(p.explanation)}</p>
        ${whyHtml(f.why)}
        ${oddsPanel(f)}
      </div>`;
  }

  function skippedHtml(data) {
    const skipped = Array.isArray(data.skipped) ? data.skipped : [];
    const notes = Array.isArray(data.notes) ? data.notes : [];
    let html = "";
    if (skipped.length || notes.length) {
      html += `<details style="font-size:0.78rem;color:#94a3b8;margin-bottom:16px;"><summary>${skipped.length} match(es) not rated · ${notes.length} note(s)</summary>
        ${notes.map((n) => `<p style="margin:6px 0;">${esc(n)}</p>`).join("")}
        ${skipped.map((s) => `<p style="margin:6px 0;">${esc(s.home_team)} vs ${esc(s.away_team)}: ${esc(s.reason)}</p>`).join("")}</details>`;
    }
    if (data.disclaimer) html += `<p style="font-size:0.72rem;color:#64748b;margin:0 0 16px;">${esc(data.disclaimer)}</p>`;
    return html;
  }

  function predictionsHtml(title, data) {
    const head = `<div style="font-size:1rem;font-weight:700;margin-bottom:12px;color:#f8fafc;">${esc(title)}</div>`;
    const list = Array.isArray(data.predictions) ? data.predictions : [];
    let html = head;
    if (list.length) html += list.map(predictionCard).join("");
    else html += statusNotice(data) || notice("🔮", "No predictions", "No matches could be rated for this date.");
    return html + skippedHtml(data);
  }

  function slipCard(slip, i) {
    const total = Number(slip.combined_odds || slip.odds || 0).toFixed(2);
    const lines = [`🎯 *OddsLab Slip ${i + 1}* (Fair Odds: ${total})\n`];
    const legs = (slip.legs || []).map((leg) => {
      const name = leg.home_team && leg.away_team ? `${leg.home_team} vs ${leg.away_team}` : leg.fixture_name || "Match";
      const market = leg.selection || leg.market || "Selection";
      const price = Number(leg.odds || 1).toFixed(2);
      const prob = leg.probability ? `${Math.round(leg.probability * 100)}%` : "";
      lines.push(`• ${name}\n  👉 Selection: ${market} @ ${price} (fair odds)`);
      const avg = leg.base_rate ? `<div style="font-size:0.72rem;color:#64748b;margin-top:2px;">Model ${Math.round((leg.probability || 0) * 100)}% vs league average ${Math.round(leg.base_rate * 100)}%</div>` : "";
      return `
        <div style="background:rgba(255,255,255,0.03);padding:8px 10px;border-radius:6px;margin-top:8px;border-left:3px solid #00E676;">
          <div style="font-weight:600;font-size:0.85rem;color:#FFFFFF;">${esc(name)}</div>
          <div style="display:flex;justify-content:space-between;font-size:0.8rem;color:#94a3b8;margin-top:2px;">
            <span>Selection: <strong style="color:#00E676;">${esc(market)}</strong></span>
            <span>Fair odds: <strong style="color:#FFFFFF;">${esc(price)}</strong> ${prob ? `(${esc(prob)})` : ""}</span>
          </div>${avg}
        </div>`;
    }).join("");
    lines.push("\nGenerated with OddsLab.");
    return `
      <div class="card" style="margin-bottom:12px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;">
          <strong style="font-size:1.05rem;">Slip ${i + 1} · Total Odds ${esc(total)}</strong>
          <span style="font-size:0.8rem;background:#0D2B1C;padding:4px 8px;border-radius:12px;color:#00E676;border:1px solid rgba(0,230,118,0.3);white-space:nowrap;">
            Model chance ${Math.round((slip.probability || 0) * 100)}%
          </span>
        </div>
        <p style="font-size:0.8rem;color:#94a3b8;margin:0 0 6px 0;">${esc(slip.note || "")}</p>
        ${legs}
        <button class="btn btn-secondary copy-slip-btn" data-text="${encodeURIComponent(lines.join("\n"))}" style="margin-top:12px;font-size:0.85rem;padding:8px;">📋 Copy Slip to Clipboard</button>
      </div>`;
  }

  function slipsHtml(data) {
    const slips = Array.isArray(data.slips) ? data.slips : [];
    let html = "";
    if (slips.length) {
      if (data.message) html += `<p style="font-size:0.8rem;color:#f59e0b;margin:0 0 10px;">${esc(data.message)}</p>`;
      html += slips.map(slipCard).join("");
    } else {
      html += statusNotice(data) ||
        notice("🔍", "No Qualified Slips Available", "Not enough qualifying matches to build a slip for this date.",
          `<button class="btn btn-secondary list-predictions-btn" style="margin:14px auto 0;font-size:0.85rem;padding:8px 12px;width:auto;display:inline-block;">🔮 See All Match Predictions</button>`);
    }
    return html + skippedHtml(data);
  }

  // ---------- odds pills ----------
  const pills = document.querySelectorAll(".odds-pill");
  pills.forEach((pill) => {
    pill.addEventListener("click", () => {
      pills.forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      selectedTargetOdds = pill.getAttribute("data-value") || "3.0";
    });
  });

  // ---------- tabs ----------
  const navItems = document.querySelectorAll(".nav-btn");
  const tabContents = document.querySelectorAll(".tab-content");
  navItems.forEach((item) => {
    item.addEventListener("click", () => {
      const tab = item.getAttribute("data-tab");
      navItems.forEach((n) => n.classList.remove("active"));
      tabContents.forEach((t) => t.classList.add("hidden"));
      item.classList.add("active");
      $(`tab-${tab}`)?.classList.remove("hidden");

      if (tab === "live") fetchLiveMatches();
      if (tab === "fixtures") fetchFixturesTab(currentDate());
      if (tab === "account") {
        const input = $("api-token-input");
        const msg = $("token-status-msg");
        const saved = localStorage.getItem("app_api_token");
        if (input) input.value = saved || "";
        if (msg) {
          msg.style.color = saved ? "#00E676" : "#f59e0b";
          msg.textContent = saved ? "✅ Token saved locally." : "⚠️ No token set.";
        }
      }
    });
  });

  // ---------- data loaders ----------
  async function fetchLiveMatches() {
    const el = $("tab-live");
    if (!el) return;
    el.innerHTML = loadingCard("Fetching today's matches...");
    try {
      const data = await api("/oa/live-fixtures");
      el.innerHTML = fixturesHtml("📍 Today's Matches (scores may be delayed)", data);
    } catch (err) {
      console.error(err);
      el.innerHTML = errorCard(`Error loading live scores: ${err.message}`);
    }
  }

  async function fetchFixturesTab(day) {
    const el = $("fixtures-container");
    if (!el) return;
    el.innerHTML = loadingCard("Loading fixtures...");
    try {
      const data = await api(`/oa/fixtures?date=${encodeURIComponent(day)}`);
      el.innerHTML = fixturesHtml(`📅 All Fixtures (${day})`, data);
    } catch (err) {
      console.error(err);
      el.innerHTML = errorCard(`Error loading fixtures: ${err.message}`);
    }
  }

  async function fetchPredictions() {
    const day = currentDate();
    const home = $("home-results");
    if (home) home.innerHTML = loadingCard("Rating every match (this can take a few seconds)...");
    try {
      const data = await api(`/oa/predictions?date=${encodeURIComponent(day)}`);
      const html = predictionsHtml(`🔮 Predictions (${day}) · ${data.count || 0} of ${data.fixtures_total || 0} matches rated`, data);
      if (home) home.innerHTML = html;
    } catch (err) {
      console.error(err);
      if (home) home.innerHTML = errorCard(`Error: ${err.message}`);
    }
  }

  async function fetchMatches(buildSlips) {
    const day = currentDate();
    const home = $("home-results");
    if (home) home.innerHTML = loadingCard("Connecting to OddsLab intelligence...");
    try {
      let html;
      if (buildSlips) {
        const data = await api(`/oa/slips?date=${encodeURIComponent(day)}&target_odds=${encodeURIComponent(selectedTargetOdds)}`);
        html = slipsHtml(data);
        if ($("slips-container")) $("slips-container").innerHTML = html;
      } else {
        const data = await api(`/oa/fixtures?date=${encodeURIComponent(day)}`);
        html = fixturesHtml(`📅 Fixtures (${day})`, data);
        if ($("fixtures-container")) $("fixtures-container").innerHTML = html;
      }
      if (home) home.innerHTML = html;
    } catch (err) {
      console.error(err);
      if (home) home.innerHTML = errorCard(`Error: ${err.message}`);
    }
  }

  async function loadPerformance() {
    const home = $("home-results");
    if (home) home.innerHTML = loadingCard("Loading performance analytics...");
    try {
      const d = await api("/oa/performance?days=30");
      if (!d.total_slips) {
        if (home) home.innerHTML = notice("📊", "No settled slips yet", "Performance appears once past slips have been saved and their matches have finished.");
        return;
      }
      const pc = d.net_profit >= 0 ? "#00E676" : "#ef4444";
      const rc = d.roi_percent >= 0 ? "#00E676" : "#ef4444";
      const box = (label, value, color = "#fff") =>
        `<div style="background:rgba(255,255,255,0.03);padding:10px;border-radius:8px;"><div style="font-size:0.75rem;color:var(--text-sub);">${label}</div><div style="font-size:1.1rem;font-weight:600;color:${color};">${esc(value)}</div></div>`;
      if (home)
        home.innerHTML = card(
          `<div style="font-size:1rem;font-weight:700;margin-bottom:12px;color:#f8fafc;">📊 30-Day Strategy Performance</div>
           <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
             ${box("Total Slips", `${d.total_slips} (${d.won_slips} Won)`)}
             ${box("Win Rate", `${d.win_rate_percent}%`)}
             ${box("Net Profit", `${d.net_profit > 0 ? "+" : ""}${d.net_profit}`, pc)}
             ${box("ROI", `${d.roi_percent > 0 ? "+" : ""}${d.roi_percent}%`, rc)}
           </div>`,
          "border:1px solid var(--accent-color);"
        );
    } catch (err) {
      console.error(err);
      if (home) home.innerHTML = errorCard(`Error loading performance: ${err.message}`);
    }
  }

  async function loadAccuracy() {
    const home = $("home-results");
    if (home) home.innerHTML = loadingCard("Replaying recent matches to measure accuracy (can take up to a minute)...");
    try {
      const runs = [["Premier League", "/oa/backtest?league=2021"],
                    ["Champions League (pooled model)", "/oa/backtest?league=2001&pooled=true"],
                    ["Champions League (single competition)", "/oa/backtest?league=2001"]];
      let html = `<div style="font-size:1rem;font-weight:700;margin-bottom:12px;color:#f8fafc;">📈 Model Accuracy Check</div>`;
      for (const [label, url] of runs) {
        const d = await api(url);
        if (d.status !== "ok") {
          html += notice("⏳", label, d.message || "Not available yet.");
          continue;
        }
        const verdict = d.better_than_baseline ? "✅ Beats the naive baseline" : "⚠️ Does NOT beat the naive baseline";
        const mkts = Object.values(d.markets || {}).map((m) => `
          <div style="margin-top:10px;font-size:0.8rem;color:#e2e8f0;">${esc(m.label)}: ${m.better_than_baseline ? "✅" : "⚠️"}
            Brier ${esc(m.model.brier)} vs baseline ${esc(m.baseline.brier)} (${esc(m.matches_scored)} matches)
            <div style="font-size:0.75rem;color:#94a3b8;">${(m.calibration || []).map((c) => `Predicted ${esc(c.predicted)} → actual ${esc(c.actual)} (${esc(c.matches)})`).join("<br>")}</div>
          </div>`).join("");
        const cal = (d.calibration || []).map((c) => `<div>Predicted ${esc(c.predicted)} → actual ${esc(c.actual)} (${esc(c.matches)} matches)</div>`).join("");
        html += card(
          `<div style="font-weight:700;margin-bottom:6px;">${esc(label)}</div>
           <div style="font-size:0.85rem;color:#e2e8f0;">${verdict}</div>
           <div style="font-size:0.8rem;color:#94a3b8;margin-top:6px;">Matches scored: ${esc(d.matches_scored)} · Top pick correct: ${esc(d.top_pick_accuracy)}</div>
           <div style="font-size:0.8rem;color:#94a3b8;margin-top:4px;">Model: RPS ${esc(d.model.rps)} · log-loss ${esc(d.model.log_loss)} · Brier ${esc(d.model.brier)}</div>
           <div style="font-size:0.8rem;color:#94a3b8;">Baseline: RPS ${esc(d.baseline.rps)} · log-loss ${esc(d.baseline.log_loss)} · Brier ${esc(d.baseline.brier)}</div>
           <div style="font-size:0.78rem;color:#94a3b8;margin-top:6px;">${cal}</div>
           ${mkts}
           <p style="font-size:0.72rem;color:#64748b;margin:8px 0 0;">${esc(d.how_to_read)}</p>`);
      }
      if (home) home.innerHTML = html;
    } catch (err) {
      console.error(err);
      if (home) home.innerHTML = errorCard(`Error: ${err.message}`);
    }
  }

  // ---------- buttons ----------
  $("slips")?.addEventListener("click", () => fetchMatches(true));
  $("load")?.addEventListener("click", () => fetchMatches(false));
  $("predict")?.addEventListener("click", fetchPredictions);
  $("accuracy-btn")?.addEventListener("click", loadAccuracy);
  $("perf-btn")?.addEventListener("click", loadPerformance);

  $("save-token-btn")?.addEventListener("click", () => {
    const input = $("api-token-input");
    const msg = $("token-status-msg");
    if (input && input.value.trim()) {
      localStorage.setItem("app_api_token", input.value.trim());
      if (msg) { msg.style.color = "#00E676"; msg.textContent = "✅ Token saved successfully!"; }
    }
  });

  $("clear-token-btn")?.addEventListener("click", () => {
    const input = $("api-token-input");
    const msg = $("token-status-msg");
    localStorage.removeItem("app_api_token");
    if (input) input.value = "";
    if (msg) { msg.style.color = "#ef4444"; msg.textContent = "🗑️ Token cleared."; }
  });

  document.addEventListener("input", (e) => {
    const el = e.target.closest && e.target.closest(".odds-input");
    if (!el) return;
    const odds = parseFloat(String(el.value).replace(",", "."));
    const out = el.parentElement.querySelector(".ev-out");
    const valid = odds > 1;
    savePrice(el.dataset.id, valid ? odds : null);
    if (out) out.innerHTML = valid ? evHtml(parseFloat(el.dataset.p), odds, el.dataset.conf, new Date().toISOString()) : "";
  });

  // Buttons created inside rendered results
  document.addEventListener("click", (e) => {
    const copy = e.target.closest(".copy-slip-btn");
    if (copy) {
      const btn = copy;
      const original = btn.textContent;
      navigator.clipboard.writeText(decodeURIComponent(btn.getAttribute("data-text"))).then(() => {
        btn.textContent = "✅ Copied!";
        btn.style.color = "#00E676";
        setTimeout(() => { btn.textContent = original; btn.style.color = ""; }, 2000);
      }).catch((err) => alert("Could not copy: " + err));
      return;
    }
    if (e.target.closest(".list-predictions-btn")) fetchPredictions();
  });
});
