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
    if (data.status === "no_predictions")
      return notice("🔍", "Not enough data to predict yet", data.message || "Matches were found but could not be modelled.");
    return "";
  }

  const crest = (url) =>
    typeof url === "string" && url.startsWith("https://")
      ? `<img src="${esc(url)}" alt="" width="20" height="20" loading="lazy" style="object-fit:contain;vertical-align:middle;margin-right:6px;">`
      : "";

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
    return `
      <div class="card" style="margin-bottom:10px;padding:12px;">
        <div style="font-size:0.75rem;color:#94a3b8;margin-bottom:6px;">${esc(f.league || "League")}</div>
        <div style="font-weight:600;font-size:0.95rem;color:#FFFFFF;">${crest(f.home_logo)}${esc(f.home_team)} vs ${crest(f.away_logo)}${esc(f.away_team)}</div>
        <div style="margin-top:8px;font-size:0.85rem;color:#94a3b8;">
          Model lean: <strong style="color:#00E676;">${esc(p.selection)}</strong>
          (${esc(p.probability_percent)}% · fair odds ${esc(p.fair_odds)})
        </div>
        <div style="font-size:0.78rem;color:#94a3b8;margin-top:4px;">xG ${esc(p.xG_home)} - ${esc(p.xG_away)} · Likely: ${scores}</div>
        <p style="font-size:0.78rem;color:#94a3b8;margin:6px 0 0 0;line-height:1.4;">${esc(p.explanation)}</p>
      </div>`;
  }

  function slipCard(slip, i) {
    const total = Number(slip.combined_odds || slip.odds || 0).toFixed(2);
    const lines = [`🎯 *OddsLab Slip ${i + 1}* (Total Odds: ${total})\n`];
    const legs = (slip.legs || []).map((leg) => {
      const name = leg.home_team && leg.away_team ? `${leg.home_team} vs ${leg.away_team}` : leg.fixture_name || "Match";
      const market = leg.selection || leg.market || "Selection";
      const price = Number(leg.odds || 1).toFixed(2);
      const prob = leg.probability ? `${Math.round(leg.probability * 100)}%` : "";
      lines.push(`• ${name}\n  👉 Selection: ${market} @ ${price}`);
      return `
        <div style="background:rgba(255,255,255,0.03);padding:8px 10px;border-radius:6px;margin-top:8px;border-left:3px solid #00E676;">
          <div style="font-weight:600;font-size:0.85rem;color:#FFFFFF;">${esc(name)}</div>
          <div style="display:flex;justify-content:space-between;font-size:0.8rem;color:#94a3b8;margin-top:2px;">
            <span>Selection: <strong style="color:#00E676;">${esc(market)}</strong></span>
            <span>Odds: <strong style="color:#FFFFFF;">${esc(price)}</strong> ${prob ? `(${esc(prob)})` : ""}</span>
          </div>
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
    const preds = Array.isArray(data.match_predictions) ? data.match_predictions : [];
    let html = "";
    if (slips.length) {
      html += slips.map(slipCard).join("");
    } else {
      html += statusNotice(data) ||
        notice("🔍", "No Qualified Slips Available", "Not enough qualifying matches to build a slip for this date.",
          `<button class="btn btn-secondary list-fixtures-btn" style="margin:14px auto 0;font-size:0.85rem;padding:8px 12px;width:auto;display:inline-block;">📋 View All Fixtures Instead</button>`);
    }
    if (preds.length) {
      html += `<div style="font-size:1rem;font-weight:700;margin:16px 0 12px;color:#f8fafc;">📈 Match Analysis (${preds.length})</div>` + preds.map(predictionCard).join("");
    }
    const skipped = Array.isArray(data.skipped) ? data.skipped.length : 0;
    const notes = Array.isArray(data.notes) ? data.notes : [];
    if (skipped || notes.length) {
      html += `<details style="font-size:0.78rem;color:#94a3b8;margin-bottom:16px;"><summary>${skipped} match(es) not modelled · ${notes.length} note(s)</summary>
        ${notes.map((n) => `<p style="margin:6px 0;">${esc(n)}</p>`).join("")}
        ${(data.skipped || []).map((s) => `<p style="margin:6px 0;">${esc(s.home_team)} vs ${esc(s.away_team)}: ${esc(s.reason)}</p>`).join("")}</details>`;
    }
    if (data.disclaimer) html += `<p style="font-size:0.72rem;color:#64748b;margin:0 0 16px;">${esc(data.disclaimer)}</p>`;
    return html;
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
      el.innerHTML = fixturesHtml("🔴 Live & Today Matches", data);
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

  // ---------- buttons ----------
  $("slips")?.addEventListener("click", () => fetchMatches(true));
  $("load")?.addEventListener("click", () => fetchMatches(false));
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
    if (e.target.closest(".list-fixtures-btn")) fetchMatches(false);
  });
});
