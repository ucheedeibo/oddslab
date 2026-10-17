const $ = s => document.querySelector(s);
const show = (id, on) => { const el = $(id); if (el) el.hidden = !on; };
const hdr = { 'Content-Type': 'application/json', 'X-Requested-With': 'oddslab' };

function text(tag, t, cls) {
  const e = document.createElement(tag);
  e.textContent = t;
  if (cls) e.className = cls;
  return e;
}

const SAMPLE_HISTORY = [
  {"date":"2025-09-01","home_team":"Arsenal","away_team":"Brighton","home_goals":2,"away_goals":1},
  {"date":"2025-09-01","home_team":"Liverpool","away_team":"Wolves","home_goals":3,"away_goals":0},
  {"date":"2025-09-02","home_team":"Man City","away_team":"Brentford","home_goals":2,"away_goals":2},
  {"date":"2025-09-13","home_team":"Chelsea","away_team":"West Ham","home_goals":1,"away_goals":0},
  {"date":"2025-09-14","home_team":"Tottenham","away_team":"Newcastle","home_goals":0,"away_goals":1},
  {"date":"2025-09-20","home_team":"Arsenal","away_team":"Leicester","home_goals":4,"away_goals":0},
  {"date":"2025-09-21","home_team":"Liverpool","away_team":"Bournemouth","home_goals":2,"away_goals":1},
  {"date":"2025-09-27","home_team":"Man City","away_team":"Forest","home_goals":3,"away_goals":1},
  {"date":"2025-09-28","home_team":"Chelsea","away_team":"Brighton","home_goals":1,"away_goals":1},
  {"date":"2025-10-04","home_team":"Arsenal","away_team":"Southampton","home_goals":2,"away_goals":0},
  {"date":"2025-10-05","home_team":"Liverpool","away_team":"Crystal Palace","home_goals":1,"away_goals":0},
  {"date":"2025-10-11","home_team":"Man City","away_team":"Fulham","home_goals":2,"away_goals":0},
  {"date":"2025-10-12","home_team":"Tottenham","away_team":"Brighton","home_goals":1,"away_goals":2},
  {"date":"2025-10-18","home_team":"Chelsea","away_team":"Liverpool","home_goals":0,"away_goals":2},
  {"date":"2025-10-19","home_team":"Arsenal","away_team":"Man City","home_goals":1,"away_goals":1}
];

async function init() {
  try {
    const r = await fetch('/oa/session');
    setAuth(r.ok);
  } catch (e) {
    console.error(e);
  }
}

function setAuth(ok) {
  show('#login', !ok);
  show('#app', ok);
  show('#logout', ok);
}

const signinBtn = $('#signin');
if (signinBtn) {
  signinBtn.onclick = async () => {
    const msg = $('#loginmsg');
    if (msg) msg.textContent = '';
    let r;
    try {
      r = await fetch('/oa/login', {
        method: 'POST',
        headers: hdr,
        body: JSON.stringify({ token: ($('#token')?.value || '').trim() })
      });
    } catch (e) {
      if (msg) msg.textContent = 'Network error';
      return;
    }
    if ($('#token')) $('#token').value = '';
    if (r.ok) {
      const chk = await fetch('/oa/session');
      if (chk.ok) setAuth(true);
      else if (msg) msg.textContent = 'Session not kept';
    } else if (r.status === 401) {
      if (msg) msg.textContent = 'Invalid token';
    } else {
      if (msg) msg.textContent = 'Server problem ' + r.status;
    }
  };
}

const logoutBtn = $('#logout');
if (logoutBtn) {
  logoutBtn.onclick = async () => {
    await fetch('/oa/logout', { method: 'POST', headers: hdr });
    setAuth(false);
  };
}

const dayInput = $('#day');
if (dayInput) dayInput.value = new Date().toISOString().slice(0, 10);

const loadBtn = $('#load');
if (loadBtn) {
  loadBtn.onclick = async () => {
    const list = $('#list');
    if (!list) return;
    list.replaceChildren(text('p', 'Loading…'));
    const r = await fetch('/oa/fixtures?day=' + encodeURIComponent(dayInput?.value || ''));
    if (r.status === 401) return setAuth(false);
    const d = await r.json();
    list.replaceChildren();
    if (!r.ok) {
      list.append(text('p', 'Could not load fixtures: ' + (d.detail || r.status), 'err'));
      return;
    }
    list.append(text('p', d.count + ' fixtures (' + d.source + ')'));

    (d.fixtures || []).forEach(f => {
      const c = document.createElement('div');
      c.className = 'card fx';
      c.append(text('span', f.league || '', 'pill'));
      const title = document.createElement('p');
      title.append(text('b', (f.home_team || '?') + ' vs ' + (f.away_team || '?')));
      c.append(title);
      const time = f.kickoff ? new Date(f.kickoff).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : 'TBC';
      c.append(text('small', time + ' · ' + (f.status || '') + ' · Prediction: not yet available'));

      const btn = document.createElement('button');
      btn.textContent = 'Get prediction';
      btn.style.marginTop = '10px';
      btn.onclick = () => getPrediction(f, c, btn);
      c.append(btn);

      list.append(c);
    });
  };
}

async function getPrediction(f, card, btn) {
  btn.disabled = true;
  btn.textContent = 'Calculating…';
  try {
    const r = await fetch('/oa/predict', {
      method: 'POST',
      headers: hdr,
      body: JSON.stringify({
        home_team: f.home_team,
        away_team: f.away_team,
        as_of: $('#day')?.value,
        history: SAMPLE_HISTORY
      })
    });
    if (r.status === 401) return setAuth(false);
    const d = await r.json();
    if (!r.ok) {
      btn.textContent = 'Error';
      card.append(text('p', d.detail || 'Failed', 'err'));
      return;
    }
    btn.remove();
    const probs = document.createElement('div');
    probs.style.display = 'flex';
    probs.style.gap = '8px';
    probs.style.marginTop = '10px';
    probs.innerHTML = `
      <div style="flex:1;text-align:center;background:#0f172a;padding:8px;border-radius:8px">
        <b>${((d.home_win || 0) * 100).toFixed(0)}%</b><br>Home
      </div>
      <div style="flex:1;text-align:center;background:#0f172a;padding:8px;border-radius:8px">
        <b>${((d.draw || 0) * 100).toFixed(0)}%</b><br>Draw
      </div>
      <div style="flex:1;text-align:center;background:#0f172a;padding:8px;border-radius:8px">
        <b>${((d.away_win || 0) * 100).toFixed(0)}%</b><br>Away
      </div>
    `;
    card.append(probs);
    if (d.warning) card.append(text('p', d.warning, 'muted'));
  } catch (e) {
    btn.textContent = 'Network error';
    btn.disabled = false;
  }
}

init();