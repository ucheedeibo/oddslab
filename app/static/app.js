const $ = s => document.querySelector(s);
const show = (id, on) => { $(id).hidden = !on; };
const hdr = { 'Content-Type': 'application/json', 'X-Requested-With': 'oddslab' };
function text(tag, t, cls) { const e = document.createElement(tag); e.textContent = t; if (cls) e.className = cls; return e; }

async function init() {
  const r = await fetch('/oa/session');
  setAuth(r.ok);
}
function setAuth(ok) { show('#login', !ok); show('#app', ok); show('#logout', ok); }

$('#signin').onclick = async () => {
  $('#loginmsg').textContent = '';
  let r;
  try {
    r = await fetch('/oa/login', { method: 'POST', headers: hdr, body: JSON.stringify({ token: $('#token').value.trim() }) });
  } catch (e) { $('#loginmsg').textContent = 'Network error: could not reach the server.'; return; }
  $('#token').value = '';
  if (r.ok) {
    const chk = await fetch('/oa/session');
    if (chk.ok) setAuth(true);
    else $('#loginmsg').textContent = 'Login accepted but the session cookie was not kept (status ' + chk.status + ').';
  } else if (r.status === 401) $('#loginmsg').textContent = 'Invalid token (server status 401).';
  else if (r.status === 429) $('#loginmsg').textContent = 'Too many attempts. Wait a few minutes.';
  else $('#loginmsg').textContent = 'Server problem, status ' + r.status + ' (not a wrong-token error).';
};
$('#logout').onclick = async () => { await fetch('/oa/logout', { method: 'POST', headers: hdr }); setAuth(false); };

$('#day').value = new Date().toISOString().slice(0, 10);
$('#load').onclick = async () => {
  const list = $('#list'); list.replaceChildren(text('p', 'Loading…'));
  const r = await fetch('/oa/fixtures?day=' + encodeURIComponent($('#day').value));
  if (r.status === 401) return setAuth(false);
  const d = await r.json();
  list.replaceChildren();
  if (!r.ok) return list.append(text('p', 'Could not load fixtures: ' + (d.detail || r.status), 'err'));
  list.append(text('p', d.count + ' fixtures (' + d.source + ')'));
  d.fixtures.forEach(f => {
    const c = document.createElement('div'); c.className = 'card fx';
    c.append(text('span', f.league, 'pill'));
    const b = document.createElement('p'); b.append(text('b', f.home_team + ' vs ' + f.away_team));
    c.append(b);
    c.append(text('small', (f.kickoff ? new Date(f.kickoff).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Time TBC') + ' · ' + f.status + ' · Prediction: not yet available'));
    list.append(c);
  });
  if (!d.count) list.append(text('p', 'No fixtures in the supported competitions on this date.'));
};
init();