document.addEventListener("DOMContentLoaded", () => {
  const tokenInput = document.getElementById("token");
  const signinBtn = document.getElementById("signin");
  const logoutBtn = document.getElementById("logout");
  const accountLogoutBtn = document.getElementById("account-logout");
  const loginSection = document.getElementById("login");
  const appSection = document.getElementById("app");
  const bottomNav = document.getElementById("bottom-nav");
  const loginErr = document.getElementById("login-err");
  const dayInput = document.getElementById("day");
  
  // Set default date to today
  const today = new Date().toISOString().split("T")[0];
  if (dayInput) dayInput.value = today;

  // Auto-login check if token exists
  const storedToken = localStorage.getItem("oddslab_token");
  if (storedToken) {
    showApp();
  }

  signinBtn.addEventListener("click", async () => {
    const val = tokenInput.value.trim();
    if (!val) {
      showError("Please enter an access code.");
      return;
    }

    loginErr.classList.add("hidden");
    signinBtn.textContent = "Verifying...";
    signinBtn.disabled = true;

    try {
      // Validate token with backend
      const res = await fetch(`/api/fixtures?date=${today}&token=${encodeURIComponent(val)}`, {
        headers: { "X-Auth-Token": val, "Authorization": `Bearer ${val}` }
      });

      const data = await res.json().catch(() => ({}));

      if (res.status === 401 || res.status === 403 || data.error?.includes("token") || data.error?.includes("auth")) {
        throw new Error("Invalid access code.");
      }

      // Valid token
      localStorage.setItem("oddslab_token", val);
      showApp();
    } catch (err) {
      showError(err.message || "Invalid access code.");
    } finally {
      signinBtn.textContent = "Unlock App";
      signinBtn.disabled = false;
    }
  });

  if (logoutBtn) logoutBtn.addEventListener("click", logout);
  if (accountLogoutBtn) accountLogoutBtn.addEventListener("click", logout);

  function logout() {
    localStorage.removeItem("oddslab_token");
    appSection.classList.add("hidden");
    bottomNav.classList.add("hidden");
    if (logoutBtn) logoutBtn.classList.add("hidden");
    loginSection.classList.remove("hidden");
    tokenInput.value = "";
    loginErr.classList.add("hidden");
  }

  function showApp() {
    loginSection.classList.add("hidden");
    appSection.classList.remove("hidden");
    bottomNav.classList.remove("hidden");
    if (logoutBtn) logoutBtn.classList.remove("hidden");
  }

  function showError(msg) {
    loginErr.textContent = msg;
    loginErr.classList.remove("hidden");
  }

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
    const token = localStorage.getItem("oddslab_token");
    const date = dayInput.value;
    const homeResults = document.getElementById("home-results");
    const slipsContainer = document.getElementById("slips-container");
    const fixturesContainer = document.getElementById("fixtures-container");

    homeResults.innerHTML = `<div class="card"><p class="empty-state">Analyzing fixtures and building slips...</p></div>`;

    try {
      const endpoint = buildSlips 
        ? `/api/slips?date=${date}&token=${encodeURIComponent(token)}` 
        : `/api/fixtures?date=${date}&token=${encodeURIComponent(token)}`;
        
      const res = await fetch(endpoint, {
        headers: { 
          "X-Auth-Token": token,
          "Authorization": `Bearer ${token}`
        }
      });

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
              <span style="font-size:0.8rem; background:#21262d; padding:4px 8px; border-radius:12px; color:var(--accent-green);">
                Est. Chance ${Math.round((slip.probability || 0) * 100)}%
              </span>
            </div>
            <p style="font-size:0.85rem; color:var(--text-muted); margin-bottom:12px;">${slip.note || 'Legs treated as independent.'}</p>
            <div style="display:flex; flex-direction:column; gap:8px;">
              ${(slip.legs || []).map(leg => `
                <div style="padding:8px; background:var(--bg-main); border-radius:6px; font-size:0.85rem;">
                  <div style="font-weight:600;">${leg.selection || 'Selection'}</div>
                  <div style="color:var(--text-muted); font-size:0.75rem;">${leg.fixture \vert{}\vert{} leg.match \vert{}\vert{} 'Match'} · Odds ${leg.odds}</div>
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
