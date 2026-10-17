const $ = s => document.querySelector(s);
const show = (id, on) => { $(id).hidden = !on; };
const hdr = { 'Content-Type': 'application/json', 'X-Requested-With': 'oddslab' };
function el(tag, t, cls) { const e = document.createElement(tag); if (t !== undefined) e.textContent = t; if (cls) e.className = cls; return e; }
const pct = x => (x * 100).toFixed(0) + '%';
const hhmm = k => k ? new Date(k).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'time TBC';

async function init() { setAuth((await fetch('/oa/session')).ok); }
function setAuth(ok) { show('#login', !ok); show('#app', ok); show('#logout', ok); }

$('#signin').onclick = async () => {
  $('#loginmsg').textContent = '';
  let r;
  try { r = await fetch('/oa/login', { method: 'POST', headers: hdr, body: JSON.stringify({ token: $('#token').value.trim() }) }); }
  catch (e) { $('#loginmsg').textContent = 'Network error: could not reach the server.'; return; }
  $('#token').value = '';
  if (r.ok) { (await fetch('/oa/session')).ok ? setAuth(true) : ($('#loginmsg').textContent = 'Session cookie was not kept.'); }
  else if (r.status === 401) $('#loginmsg').textContent = 'Invalid token (server status 401).';
  else if (r.status === 429) $('#loginmsg').textContent = 'Too many attempts. Wait a few minutes.';
  else $('#loginmsg').textContent = 'Server problem, status ' + r.status + '.';
};
$('#logout').onclick = async () => { await fetch('/oa/logout', { method: 'POST', headers: hdr }); setAuth(false); };
$('#day').value = new Date().toISOString().slice(0, 10);

async function call(url) {
  const list = $('#list'); list.replaceChildren(el('p', 'Working… the first run of the day can take up to a minute.'));
  const r = await fetch(url);
  if (r.status === 401) { setAuth(false); return null; }
  const d = await r.json().catch(() => ({}));
  list.replaceChildren();
  if (!r.ok) { list.append(el('p', 'Problem: ' + (d.detail || r.status), 'err')); return null; }
  return d;
}

$('#load').onclick = async () => {
  const d = await call('/oa/fixtures?day=' + encodeURIComponent($('#day').value)); if (!d) return;
  const list = $('#list'); list.append(el('p', d.count + ' fixtures (' + d.source + ')'));
  d.fixtures.forEach(f => {
    const c = el('div', undefined, 'card');
    c.append(el('span', f.league, 'pill'), el('p', f.home_team + ' vs ' + f.away_team));
    c.append(el('small', hhmm(f.kickoff) + ' · ' + f.status)); list.append(c);
  });
};

$('#slips').onclick = async () => {
  const d = await call('/oa/slips?day=' + encodeURIComponent($('#day').value)); if (!d) return;
  const list = $('#list');
  list.append(el('p', d.games_considered + ' games analysed · ' + d.slips.length + ' slip(s) · target ' + d.target_odds.toFixed(2)));
  if (!d.slips.length) list.append(el('div', 'No slip could be built from today\'s games (too few games with usable odds and model data). Nothing is forced.', 'card warn'));
  d.slips.forEach((s, i) => {
    const c = el('div', undefined, 'card slip' + (s.value_flag ? '' : ' noval'));
    const top = el('div', undefined, 'row');
    top.append(el('b', 'Slip ' + (i + 1) + ' · odds ' + s.combined_odds.toFixed(2)), el('span', 'est. chance ' + pct(s.probability), 'tag'));
    c.append(top);
    c.append(el('small', s.value_flag ? 'Estimated value after allowing for uncertainty: ' + (s.ev_conservative * 100).toFixed(0) + '% per unit staked (an estimate, not a promise)'
      : 'No estimated edge after allowing for uncertainty (best available for this price, not a recommendation)'));
    s.legs.forEach(l => {
      const g = el('div', undefined, 'leg');
      g.append(el('b', l.label), el('small', l.match + ' · ' + l.league + ' · ' + hhmm(l.kickoff)));
      const meta = 'odds ' + l.odds.toFixed(2) + ' · model ' + pct(l.p) + (l.p_market != null ? ' · market ' + pct(l.p_market) : '') + ' · ' + l.n_books + ' book(s)';
      g.append(el('small', meta));
      if (l.warn) g.append(el('small', l.warn, 'err'));
      c.append(g);
    });
    c.append(el('small', 'Legs treated as independent (different matches). ' + s.warnings.join(' ')));
    list.append(c);
  });
  const more = el('div', undefined, 'card');
  more.append(el('small', d.disclaimer));
  if (d.skipped.length) more.append(el('small', 'Skipped: ' + d.skipped.slice(0, 6).join(' | ')));
  if (d.notes.length) more.append(el('small', 'Notes: ' + d.notes.join(' | ')));
  const un = Object.keys(d.unmapped_bet_types || {});
  if (un.length) more.append(el('small', 'Bet types seen but not usable yet (no model): ' + un.join(', ')));
  more.append(el('small', 'Provider requests this session: ' + d.provider_requests_this_session));
  list.append(more);
};
init();
