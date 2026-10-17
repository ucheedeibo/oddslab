const $ = s => document.querySelector(s);
const show = (id, on) => { $(id).hidden = !on; };
const hdr = { 'Content-Type': 'application/json', 'X-Requested-With': 'oddslab' };

function text(tag, t, cls) {
  const e = document.createElement(tag);
  e.textContent = t;
  if (cls) e.className = cls;
  return e;
}

// Temporary sample history so predictions work today
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
  const r = await fetch('/oa/session');
  setAuth(r.ok);
}

function setAuth(ok) {
  show('#login', !ok);
  show('#app', ok);
  show('#logout', ok);
}

$('#signin').onclick = async () => {
  $('#loginmsg').textContent = '';
  let r;
  try {
    r = await fetch('/oa/login', {
      method: 'POST',
      headers: hdr,
      body: JSON.stringify({ token: $('#token').value.trim() })
    });
  } catch (e) {
    $('#loginmsg').textContent = 'Network error: could not reach the server.';
    return;
  }
  $('#token').value = '';
  if (r.ok) {
    const chk = await fetch('/oa/session');
    if (chk.ok) setAuth(true);
    else $('#loginmsg').textContent = 'Login accepted but the session cookie was not kept.';
  } else if (r.status === 401) {
    $('#loginmsg').textContent = 'Invalid token.';
  } else if (r.status === 429) {
    $('#loginmsg').textContent = 'Too many attempts. Wait a few minutes.';
  } else {
    $('#loginmsg').textContent = 'Server problem, status ' + r.status;
  }
};

$('#logout').onclick = async () => {
  await fetch('/oa/logout', { method: 'POST', headers: hdr });
  setAuth(false);
};

$('#day').value = new Date().toISOString().slice(0, 10);

$('#load').onclick = async () => {
  const list = $('#list');
  list.replaceChildren(text('p', 'Loading…'));
  const r = await fetch('/oa/fixtures?day=' + encodeURIComponent($('#day').value));
  if (r.status === 401) return setAuth(false);
  const d = await r.json();
  list.replaceChildren();
  if (!r.ok) {
    return list.append(text('p', 'Could not load fixtures: ' + (d.detail || r.status), 'err'));
  }
  list.append(text('p', d.count + ' fixtures (' + d.source + ')', 'muted'));

  d.fixtures.forEach(f => {
    const c = document.createElement('div');
    c.className = 'card fx';
    c.append(text('span', f.league || 'League', 'pill'));

    const title = document.createElement('p');
    title.append(text('b', (f.home_team || '?') + ' vs ' + (f.away_team || '?')));
    c.append(title);

    const time = (f.kickoff ? new Date(f.kickoff).toLocaleTimeString([], {hour:'2-digit', minute:'2-