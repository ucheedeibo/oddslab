document.addEventListener("DOMContentLoaded", () => {
  const dayInput = document.getElementById("day");
  const today = new Date().toISOString().split("T")[0];
  if (dayInput) dayInput.value = today;

  const slipsBtn = document.getElementById("slips");
  const loadBtn = document.getElementById("load");

  if (slipsBtn) {
    slipsBtn.addEventListener("click", () => fetchMatches(true));
  }
  if (loadBtn) {
    loadBtn.addEventListener("click", () => fetchMatches(false));
  }

  async function fetchMatches(buildSlips) {
    const date = dayInput ? dayInput.value : today;
    const homeResults = document.getElementById("home-results");
    const slipsContainer = document.getElementById("slips-container");
    const fixturesContainer = document.getElementById("fixtures-container");

    if (homeResults) {
      homeResults.innerHTML = `<div class="card"><p style="color:#9CA3AF; text-align:center; margin:0;">Connecting to MatchMind intelligence...</p></div>`;
    }

    try {
      const endpoint = buildSlips 
        ? `/oa/slips?date=${date}` 
        : `/oa/fixtures?date=${date}`;
        
      const res = await fetch(endpoint, {
        headers: {
          "x-requested-with": "oddslab"
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
        throw new Error("Unauthorized. Please authenticate or check your APP_API_TOKEN.");
      }

      if (!res.ok || data.error) {
        throw new Error(data.error || `Server error: ${res.status}`);
      }

      let html = '';
      if (Array.isArray(data.fixtures) && data.fixtures.length > 0) {
        html = data.fixtures.map((f) => `
          <div class="card" style="margin-bottom: 10px; padding: 12px;">
            <div style="font-size: 0.75rem; color: #9CA3AF; margin-bottom: 4px;">${f.league || 'League'}</div>
            <div style="font-weight: 600; font-size: 0.95rem; color: #FFFFFF;">${f.home_team} vs ${f.away_team}</div>
            <div style="font-size: 0.75rem; color: #00E676; margin-top: 6px;">Status: ${f.status || 'Scheduled'}</div>
          </div>
        `).join('');
      } else if (Array.isArray(data.slips) && data.slips.length > 0) {
        html = data.slips.map((slip, i) => `
          <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
              <strong style="font-size:1.1rem;">Slip ${i + 1} · Odds ${(slip.combined_odds || slip.odds || 0).toFixed(2)}</strong>
              <span style="font-size:0.8rem; background:#0D2B1C; padding:4px 8px; border-radius:12px; color:#00E676; border: 1px solid rgba(0,230,118,0.3);">
                Est. Chance ${Math.round((slip.probability || 0) * 100)}%
              </span>
            </div>
            <p style="font-size:0.85rem; color:#9CA3AF; margin:0;">${slip.note || ''}</p>
          </div>
        `).join('');
      } else {
        html = `<div class="card"><pre style="white-space: pre-wrap; font-size: 0.85rem; color: #9CA3AF; margin:0;">${JSON.stringify(data, null, 2)}</pre></div>`;
      }

      if (homeResults) homeResults.innerHTML = html;
      if (buildSlips && slipsContainer) {
        slipsContainer.innerHTML = html;
      } else if (!buildSlips && fixturesContainer) {
        fixturesContainer.innerHTML = html;
      }

    } catch (err) {
      const errHtml = `<div class="card"><p style="color: #EF4444; font-size: 0.85rem; margin:0;">Error: ${err.message}</p></div>`;
      if (homeResults) homeResults.innerHTML = errHtml;
    }
  }
});
