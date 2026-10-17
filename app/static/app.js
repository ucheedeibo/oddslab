document.addEventListener("DOMContentLoaded", () => {
  const dayInput = document.getElementById("day");
  
  // Default to today's date
  const today = new Date().toISOString().split("T")[0];
  if (dayInput) dayInput.value = today;

  // Bottom Navigation Switcher
  const navItems = document.querySelectorAll(".nav-item");
  const tabContents = document.querySelectorAll(".tab-content");

  navItems.forEach((item) => {
    item.addEventListener("click", () => {
      const targetTab = item.getAttribute("data-tab");

      navItems.forEach((nav) => nav.classList.remove("active"));
      tabContents.forEach((tab) => tab.classList.add("hidden"));

      item.classList.add("active");
      const activeTabEl = document.getElementById(`tab-${targetTab}`);
      if (activeTabEl) {
        activeTabEl.classList.remove("hidden");
      }
    });
  });

  // Action Buttons
  const slipsBtn = document.getElementById("slips");
  const loadBtn = document.getElementById("load");

  if (slipsBtn) {
    slipsBtn.addEventListener("click", () => fetchMatches(true));
  }
  if (loadBtn) {
    loadBtn.addEventListener("click", () => fetchMatches(false));
  }

  async function fetchMatches(buildSlips) {
    const date = dayInput.value;
    const homeResults = document.getElementById("home-results");
    const slipsContainer = document.getElementById("slips-container");
    const fixturesContainer = document.getElementById("fixtures-container");

    homeResults.innerHTML = `<div class="card"><p class="empty-state">Analyzing fixtures and building slips...</p></div>`;

    try {
      const endpoint = buildSlips 
        ? `/api/slips?date=${date}` 
        : `/api/fixtures?date=${date}`;
        
      const res = await fetch(endpoint);
      const text = await res.text();
      let data;
      try {
        data = JSON.parse(text);
      } catch (e) {
        throw new Error(`Server returned non-JSON response (${res.status})`);
      }

      if (!res.ok || data.error) {
        throw new Error(data.error || `Server error: ${res.status}`);
      }

      let html = '';
      if (Array.isArray(data.slips) && data.slips.length > 0) {
        html = data.slips.map((slip, i) => `
          <div class="card">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
              <strong style="font-size:1.1rem;">Slip ${i + 1} · Odds ${(slip.combined_odds || slip.odds || 0).toFixed(2)}</strong>
              <span style="font-size:0.8rem; background:#0D2B1C; padding:4px 8px; border-radius:12px; color:#00E676; border: 1px solid rgba(0,230,118,0.3);">
                Est. Chance ${Math.round((slip.probability || 0) * 100)}%
              </span>
            </div>
            <p style="font-size:0.85rem; color:#9CA3AF; margin-bottom:12px;">${slip.note || 'Legs treated as independent.'}</p>
            <div style="display:flex; flex-direction:column; gap:8px;">
              ${(slip.legs || []).map(leg => `
                <div style="padding:8px; background:#0B0D0F; border-radius:6px; font-size:0.85rem;">
                  <div style="font-weight:600;">${leg.selection || 'Selection'}</div>
                  <div style="color:#9CA3AF; font-size:0.75rem;">${leg.fixture \vert{}\vert{} leg.match \vert{}\vert{} 'Match'} · Odds ${leg.odds}</div>
                </div>
              `).join('')}
            </div>
          </div>
        `).join('');
      } else {
        html = `<div class="card"><pre style="white-space: pre-wrap; font-size: 0.85rem;">${JSON.stringify(data, null, 2)}</pre></div>`;
      }

      homeResults.innerHTML = html;
      if (buildSlips) {
        slipsContainer.innerHTML = html;
      } else {
        fixturesContainer.innerHTML = html;
      }

    } catch (err) {
      homeResults.innerHTML = `<div class="card"><p class="error-msg">Problem: ${err.message}</p></div>`;
    }
  }
});
