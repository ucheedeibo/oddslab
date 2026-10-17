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
  
  // Default to today's date
  const today = new Date().toISOString().split("T")[0];
  if (dayInput) dayInput.value = today;

  // Restore session
  const storedToken = localStorage.getItem("oddslab_token");
  if (storedToken) {
    showApp();
  }

  signinBtn.addEventListener("click", () => {
    const val = tokenInput.value.trim();
    if (val) {
      localStorage.setItem("oddslab_token", val);
      showApp();
    } else {
      showError("Please enter a valid token.");
    }
  });

  logoutBtn.addEventListener("click", logout);
  accountLogoutBtn.addEventListener("click", logout);

  function logout() {
    localStorage.removeItem("oddslab_token");
    appSection.classList.add("hidden");
    bottomNav.classList.add("hidden");
    logoutBtn.classList.add("hidden");
    loginSection.classList.remove("hidden");
    tokenInput.value = "";
  }

  function showApp() {
    loginSection.classList.add("hidden");
    appSection.classList.remove("hidden");
    bottomNav.classList.remove("hidden");
    logoutBtn.classList.remove("hidden");
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

    homeResults.innerHTML = `<div class="card"><p class="empty-state">Loading data...</p></div>`;

    try {
      const endpoint = buildSlips ? `/api/slips?date=${date}` : `/api/fixtures?date=${date}`;
      const res = await fetch(endpoint, {
        headers: { "X-Auth-Token": token }
      });
      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || "Failed to fetch data.");
      }

      const formattedData = `<div class="card"><pre style="white-space: pre-wrap; font-size: 0.85rem;">${JSON.stringify(data, null, 2)}</pre></div>`;
      
      homeResults.innerHTML = formattedData;
      if (buildSlips) {
        slipsContainer.innerHTML = formattedData;
      } else {
        fixturesContainer.innerHTML = formattedData;
      }

    } catch (err) {
      homeResults.innerHTML = `<div class="card"><p class="error-msg">Problem: ${err.message}</p></div>`;
    }
  }
});
