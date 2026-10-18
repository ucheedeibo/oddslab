document.addEventListener("DOMContentLoaded", () => {
  const dayInput = document.getElementById("day");
  const today = new Date().toISOString().split("T")[0];
  if (dayInput) dayInput.value = today;

  // Selected Target Odds State
  let selectedTargetOdds = "3.0";

  // Odds Pill Selector Event Listener
  const pills = document.querySelectorAll(".odds-pill");
  pills.forEach((pill) => {
    pill.addEventListener("click", () => {
      pills.forEach((p) => p.classList.remove("active"));
      pill.classList.add("active");
      selectedTargetOdds = pill.getAttribute("data-value") || "3.0";
    });
  });

  // Bottom Navigation Handler
  const navItems = document.querySelectorAll(".nav-btn");
  const tabContents = document.querySelectorAll(".tab-content");

  navItems.forEach((item) => {
    item.addEventListener("click", async () => {
      const targetTab = item.getAttribute("data-tab");

      navItems.forEach((nav) => nav.classList.remove("active"));
      tabContents.forEach((tab) => tab.classList.add("hidden"));

      item.classList.add("active");
      const activeTabEl = document.getElementById(`tab-${targetTab}`);
      if (activeTabEl) {
        activeTabEl.classList.remove("hidden");
      }

      // Auto-fetch Live Matches on Live tab click
      if (targetTab === "live") {
        fetchLiveMatches();
      }

      // Account tab token sync
      if (targetTab === "account") {
        const input = document.getElementById("api-token-input");
        const msg = document.getElementById("token-status-msg");
        const savedToken = localStorage.getItem("app_api_token");
        if (input) input.value = savedToken || "";
        if (msg) msg.textContent = savedToken ? "✅ Token saved locally." : "⚠️ No token set.";
      }
    });
  });

  const slipsBtn = document.getElementById("slips");
  const loadBtn = document.getElementById("load");

  if (slipsBtn) slipsBtn.addEventListener("click", () => fetchMatches(true));
  if (loadBtn) loadBtn.addEventListener("click", () => fetchMatches(false));

  async function fetchLiveMatches() {
    const liveContainer = document.getElementById("tab-live");
    if (!liveContainer) return;

    liveContainer.innerHTML = `<div class="card"><p style="color:#94a3b8; text-align:center; margin:0;">Fetching live scores...</p></div>`;

    try {
      let token = localStorage.getItem("app_api_token");
      const date = dayInput ? dayInput.value : today;

      const res = await fetch(`/oa/fixtures?date=${date}`, {
        headers: {
          "x-requested-with": "oddslab",
          "Authorization": `Bearer ${token || ""}`,
          "x-api-token": token || ""
        }
      });

      const text = await res.text();
      let data;
      try {
        data = JSON.parse(text);
      } catch (e) {
        throw new Error(`Server response: ${text.slice(0, 100)}`);
      }

      if (!res.ok) throw new Error(data.error || `Server error: ${res.status}`);

      let html = '<div style="font-size: 1rem; font-weight: 700; margin-bottom: 12px; color: #f8fafc;">🔴 Live Matches Today</div>';

      if (Array.isArray(data.fixtures) && data.fixtures.length > 0) {
        html += data.fixtures.map((f) => {
          const hScore = f.home_score ?? f.goals_home ?? f.home_goals ?? f.goals?.home;
          const aScore = f.away_score ?? f.goals_away ?? f.away_goals ?? f.goals?.away;
          
          const hasScores = (hScore !== undefined && hScore !== null) && (aScore !== undefined && aScore !== null);
          const scoreDisplay = hasScores ? `${hScore} - ${aScore}` : 'VS';
          const statusText = f.status || 'FT';

          return `
            <div class="card" style="margin-bottom: 10px; padding: 12px;">
              <div style="font-size: 0.75rem; color: #94a3b8; margin-bottom: 6px; display: flex; justify-content: space-between;">
                <span>${f.league || "League"}</span>
                <span style="color: #00E676; font-weight: 600;">${statusText}</span>
              </div>
              <div style="font-weight: 600; font-size: 0.95rem; color: #FFFFFF; display: flex; justify-content: space-between; align-items: center;">
                <span>${f.home_team} vs ${f.away_team}</span>
                <span style="background: rgba(0,230,118,0.1); color: #00E676; padding: 3px 8px; border-radius: 6px; font-size: 0.85rem; font-weight: 700; border: 1px solid rgba(0,230,118,0.2);">
                  ${scoreDisplay}
                </span>
              </div>
            </div>
          `;
        }).join("");
      } else {
        html += `
          <div class="card" style="text-align: center; padding: 20px 16px;">
            <div style="font-size: 1.8rem; margin-bottom: 8px;">⚽</div>
            <div style="font-size: 1rem; font-weight: 700; color: #f8fafc; margin-bottom: 6px;">No Live Fixtures Today</div>
            <p style="font-size: 0.85rem; color: #94a3b8; margin: 0; line-height: 1.4;">
              There are no matches scheduled or active for today's date.
            </p>
          </div>
        `;
      }

      liveContainer.innerHTML = html;
    } catch (err) {
      console.error(err);
      liveContainer.innerHTML = `<div class="card"><p style="color: #ef4444; font-size: 0.85rem; margin:0;">Error loading live scores: ${err.message}</p></div>`;
    }
  }

  async function fetchMatches(buildSlips) {
    const date = dayInput ? dayInput.value : today;

    const homeResults = document.getElementById("home-results");
    const slipsContainer = document.getElementById("slips-container");
    const fixturesContainer = document.getElementById("fixtures-container");

    let token = localStorage.getItem("app_api_token");
    if (!token) {
      token = prompt("Please enter your APP_API_TOKEN from Render:");
      if (token) localStorage.setItem("app_api_token", token.trim());
    }

    const loadingHtml = `<div class="card"><p style="color:#94a3b8; text-align:center; margin:0;">Connecting to OddsLab intelligence...</p></div>`;
    if (homeResults) homeResults.innerHTML = loadingHtml;

    try {
      const endpoint = buildSlips 
        ? `/oa/slips?date=${date}&target_odds=${selectedTargetOdds}` 
        : `/oa/fixtures?date=${date}`;
        
      const res = await fetch(endpoint, {
        headers: {
          "x-requested-with": "oddslab",
          "Authorization": `Bearer ${token || ""}`,
          "x-api-token": token || ""
        }
      });
      
      const text = await res.text();
      let data;
      try {
        data = JSON.parse(text);
      } catch (e) {
        throw new Error(`Server returned non-JSON response (${res.status})`);
      }

      if (res.status === 401) {
        localStorage.removeItem("app_api_token");
        throw new Error("Unauthorized (401). Please check your APP_API_TOKEN.");
      }

      if (!res.ok || data.error) {
        throw new Error(data.error || `Server error: ${res.status}`);
      }

      let html = '';
      if (Array.isArray(data.fixtures) && data.fixtures.length > 0) {
        html = data.fixtures.map((f) => `
          <div class="card" style="margin-bottom: 10px; padding: 12px;">
            <div style="font-size: 0.75rem; color: #94a3b8; margin-bottom: 4px;">${f.league || 'League'}</div>
            <div style="font-weight: 600; font-size: 0.95rem; color: #FFFFFF;">${f.home_team} vs ${f.away_team}</div>
            <div style="font-size: 0.75rem; color: #00E676; margin-top: 6px;">Status: ${f.status || 'Scheduled'}</div>
          </div>
        `).join('');
      } else if (Array.isArray(data.slips) && data.slips.length > 0) {
        html = data.slips.map((slip, i) => {
          let shareTextLines = [`🎯 *OddsLab Slip ${i + 1}* (Total Odds: ${(slip.combined_odds || slip.odds || 0).toFixed(2)})\n`];

          const legsHtml = (slip.legs || []).map((leg) => {
            const home = leg.home_team || (leg.fixture ? leg.fixture.home_team : null);
            const away = leg.away_team || (leg.fixture ? leg.fixture.away_team : null);
            const matchName = (home && away) ? `${home} vs ${away}` : (leg.fixture_name || leg.match || 'Match');
            const market = leg.label || leg.selection || leg.market || leg.type || 'Selection';
            const price = (leg.odds || leg.price || 1.0).toFixed(2);
            const prob = leg.probability ? `${Math.round(leg.probability * 100)}%` : '';

            shareTextLines.push(`• ${matchName}\n  👉 Selection: ${market} @ ${price}`);

            return `
              <div style="background: rgba(255,255,255,0.03); padding: 8px 10px; border-radius: 6px; margin-top: 8px; border-left: 3px solid #00E676;">
                <div style="font-weight: 600; font-size: 0.85rem; color: #FFFFFF;">${matchName}</div>
                <div style="display: flex; justify-content: space-between; font-size: 0.8rem; color: #94a3b8; margin-top: 2px;">
                  <span>Selection: <strong style="color: #00E676;">${market}</strong></span>
                  <span>Odds: <strong style="color: #FFFFFF;">${price}</strong> ${prob ? `(${prob})` : ''}</span>
                </div>
              </div>
            `;
          }).join('');

          shareTextLines.push(`\nGenerated with OddsLab.`);
          const fullShareText = encodeURIComponent(shareTextLines.join('\n'));

          return `
            <div class="card" style="margin-bottom: 12px;">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                <strong style="font-size:1.1rem;">Slip ${i + 1} · Total Odds ${(slip.combined_odds || slip.odds || 0).toFixed(2)}</strong>
                <span style="font-size:0.8rem; background:#0D2B1C; padding:4px 8px; border-radius:12px; color:#00E676; border: 1px solid rgba(0,230,118,0.3);">
                  Est. Chance ${Math.round((slip.probability || 0) * 100)}%
                </span>
              </div>
              <p style="font-size:0.8rem; color:#94a3b8; margin: 0 0 6px 0;">${slip.note || ''}</p>
              ${legsHtml}
              <button class="btn btn-secondary copy-slip-btn" data-text="${fullShareText}" style="margin-top: 12px; font-size: 0.85rem; padding: 8px;">
                📋 Copy Slip to Clipboard
              </button>
            </div>
          `;
        }).join('');
      } else {
        html = `
          <div class="card" style="text-align: center; padding: 20px 16px;">
            <div style="font-size: 1.8rem; margin-bottom: 8px;">🔍</div>
            <div style="font-size: 1rem; font-weight: 700; color: #f8fafc; margin-bottom: 6px;">No Qualified Slips Available</div>
            <p style="font-size: 0.85rem; color: #94a3b8; margin: 0 0 14px 0; line-height: 1.4;">
              ${data.games_considered === 0 
                ? "There are no remaining eligible matches for this date." 
                : "Match criteria or target odds filters couldn't find a matching combination for today."}
            </p>
            <button class="btn btn-secondary" id="empty-list-btn" style="font-size: 0.85rem; padding: 8px 12px; margin: 0 auto; width: auto; display: inline-block;">
              📋 View All Fixtures Instead
            </button>
          </div>
        `;
      }

      if (homeResults) homeResults.innerHTML = html;
      if (buildSlips && slipsContainer) slipsContainer.innerHTML = html;
      else if (!buildSlips && fixturesContainer) fixturesContainer.innerHTML = html;

      document.getElementById('empty-list-btn')?.addEventListener('click', () => fetchMatches(false));

      document.querySelectorAll('.copy-slip-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const rawText = decodeURIComponent(e.currentTarget.getAttribute('data-text'));
          navigator.clipboard.writeText(rawText).then(() => {
            const originalText = e.currentTarget.textContent;
            e.currentTarget.textContent = "✅ Copied!";
            e.currentTarget.style.color = "#00E676";
            setTimeout(() => {
              e.currentTarget.textContent = originalText;
              e.currentTarget.style.color = "";
            }, 2000);
          }).catch(err => alert("Could not copy: " + err));
        });
      });

    } catch (err) {
      const errHtml = `<div class="card"><p style="color: #EF4444; font-size: 0.85rem; margin:0;">Error: ${err.message}</p></div>`;
      if (homeResults) homeResults.innerHTML = errHtml;
    }
  }
});
