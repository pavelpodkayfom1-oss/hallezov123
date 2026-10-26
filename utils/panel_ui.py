"""HTML-интерфейс веб-панели (одна страница, без внешних зависимостей).

Плейсхолдер __NONCE__ подставляется сервером при каждой отдаче страницы
(нужен для строгой Content-Security-Policy).
Весь вывод данных идёт через textContent, поэтому текст из логов и Discord
не может внедрить HTML/скрипты.
"""

INDEX_HTML = r"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Панель управления ботом</title>
<style nonce="__NONCE__">
:root{
  --bg:#0d0d10; --panel:#16161a; --panel2:#1d1d23; --line:#2a2a32;
  --text:#ececf1; --muted:#8e8e9a; --accent:#990000; --accent2:#d23a3a;
  --ok:#2ecc71; --warn:#f39c12; --err:#e74c3c; --radius:12px;
}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
h1,h2,h3{margin:0 0 .4em;font-weight:650;letter-spacing:-.01em}
h1{font-size:1.5rem} h2{font-size:1.15rem} h3{font-size:1rem}
p{margin:.3em 0 .9em}
.muted{color:var(--muted)} .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.small{font-size:.85rem}
button,input,select,textarea{font:inherit;color:inherit}
input,select,textarea{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:.55em .75em;min-width:0}
input:focus,select:focus,textarea:focus,button:focus-visible{outline:2px solid var(--accent2);outline-offset:1px}
.btn{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:.5em 1em;cursor:pointer;transition:background .15s,border-color .15s}
.btn:hover{border-color:#44444f;background:#24242b}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn.primary{background:var(--accent);border-color:var(--accent)}
.btn.primary:hover{background:#b30000}
.btn.danger{background:#3a1212;border-color:#6b1c1c;color:#ffb4b4}
.btn.danger:hover{background:#4d1717}
.btn.wide{width:100%}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:1.1rem 1.2rem}
.row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.row.end{justify-content:flex-end}
.err{color:var(--err);min-height:1.4em;margin-top:.5rem;font-size:.9rem}

/* вход */
.center{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:1rem}
.login{width:100%;max-width:360px;text-align:center;display:flex;flex-direction:column;gap:.7rem;padding:2rem 1.6rem}
.login input{width:100%;text-align:center}
.logo{width:54px;height:54px;border-radius:15px;background:var(--accent);display:grid;place-items:center;font-size:1.6rem;font-weight:700;margin:0 auto .3rem}
.spinner{width:34px;height:34px;border:3px solid var(--line);border-top-color:var(--accent2);border-radius:50%;margin:0 auto;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* каркас */
.shell{display:grid;grid-template-columns:230px 1fr;min-height:100vh}
.side{background:var(--panel);border-right:1px solid var(--line);padding:1.1rem .8rem;display:flex;flex-direction:column;gap:.3rem;position:sticky;top:0;height:100vh}
.brand{display:flex;gap:.7rem;align-items:center;padding:.2rem .5rem 1rem}
.brand .logo{width:38px;height:38px;border-radius:11px;font-size:1.1rem;margin:0}
.brand b{display:block;line-height:1.2} .brand span{font-size:.78rem;color:var(--muted)}
.nav{display:flex;flex-direction:column;gap:.2rem}
.nav button{background:none;border:0;text-align:left;padding:.65em .8em;border-radius:9px;cursor:pointer;color:var(--muted)}
.nav button:hover{background:var(--panel2);color:var(--text)}
.nav button.active{background:rgba(153,0,0,.28);color:#fff;box-shadow:inset 3px 0 0 var(--accent2)}
.spacer{flex:1}
.main{padding:1.6rem clamp(1rem,3vw,2.2rem);max-width:1100px;width:100%}
.head{display:flex;justify-content:space-between;align-items:flex-end;gap:1rem;margin-bottom:1.2rem;flex-wrap:wrap}
.head p{margin:0}

/* обзор */
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.8rem;margin-bottom:1.4rem}
.stat{padding:.9rem 1rem}
.stat small{display:block;color:var(--muted);font-size:.8rem;margin-bottom:.15rem}
.stat b{font-size:1.35rem;font-weight:650}
.stat.ok b{color:var(--ok)} .stat.warn b{color:var(--warn)}
.guilds{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:.8rem}
.guild{display:flex;gap:.8rem;align-items:center;padding:.8rem 1rem}
.guild img,.ava{width:42px;height:42px;border-radius:12px;background:var(--panel2);object-fit:cover;flex:none}
.ava{display:grid;place-items:center;font-weight:650;color:var(--muted)}
.guild b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.section-title{margin:1.6rem 0 .7rem;color:var(--muted);font-size:.8rem;text-transform:uppercase;letter-spacing:.08em}

/* модули */
.list{display:flex;flex-direction:column;gap:.5rem}
.item{display:flex;align-items:center;gap:.8rem;padding:.7rem 1rem}
.item .grow{flex:1;min-width:0}
.item .grow b{display:block;overflow:hidden;text-overflow:ellipsis}
.item.dim{opacity:.6}
.chip{display:inline-block;font-size:.72rem;padding:.12em .6em;border-radius:99px;border:1px solid var(--line);color:var(--muted);vertical-align:middle;margin-left:.4rem}
.chip.ok{color:var(--ok);border-color:rgba(46,204,113,.4)}
.chip.err{color:var(--err);border-color:rgba(231,76,60,.45)}
.chip.off{color:var(--muted)}
.switch{width:46px;height:26px;border-radius:99px;border:1px solid var(--line);background:#2a2a32;position:relative;cursor:pointer;flex:none;padding:0;transition:background .15s}
.switch::after{content:"";position:absolute;top:2px;left:2px;width:20px;height:20px;border-radius:50%;background:#cfcfd8;transition:transform .15s}
.switch.on{background:var(--accent);border-color:var(--accent)}
.switch.on::after{transform:translateX(20px);background:#fff}
.switch:disabled{opacity:.4;cursor:not-allowed}
.toolbar{display:flex;gap:.6rem;margin-bottom:.9rem;flex-wrap:wrap}
.toolbar input[type=search]{flex:1;min-width:180px}

/* настройки */
.split{display:grid;grid-template-columns:210px 1fr;gap:1rem;align-items:start}
.files{display:flex;flex-direction:column;gap:.3rem}
.files button{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:.5em .8em;text-align:left;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.files button.active{border-color:var(--accent2);background:rgba(153,0,0,.2)}
.editor{width:100%;min-height:62vh;resize:vertical;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.85rem;line-height:1.55;white-space:pre;tab-size:2}
.note{border-left:3px solid var(--accent2);padding:.2rem .9rem;color:var(--muted);font-size:.88rem;margin-bottom:.9rem}

/* логи */
.logbox{background:#09090b;border:1px solid var(--line);border-radius:var(--radius);height:62vh;overflow:auto;padding:.7rem .9rem;font-size:.8rem;line-height:1.5}
.ln{white-space:pre-wrap;word-break:break-word;color:#c9c9d2}
.ln.e{color:#ff8a80} .ln.w{color:#ffcc80} .ln.o{color:#8be0a8}

/* управление */
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1rem}
.form{display:flex;flex-direction:column;gap:.7rem}
.form label{display:flex;flex-direction:column;gap:.25rem;font-size:.85rem;color:var(--muted)}
.card.danger-zone{border-color:#5a1b1b}

/* заявки, сообщения, экраны, диагностика */
.pager{display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:.8rem}
.pager button{background:var(--panel2);border:1px solid var(--line);border-radius:99px;padding:.35em .9em;cursor:pointer;color:var(--muted);font-size:.85rem}
.pager button.active{border-color:var(--accent2);color:#fff;background:rgba(153,0,0,.25)}
.rows{display:flex;flex-direction:column;gap:.4rem}
.r{display:flex;gap:.7rem;align-items:center;padding:.6rem .85rem;background:var(--panel);border:1px solid var(--line);border-radius:10px;cursor:pointer;text-align:left;width:100%}
.r:hover{border-color:#44444f}
.r.sel{border-color:var(--accent2);background:rgba(153,0,0,.14)}
.r .grow{flex:1;min-width:0}
.r b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
.r small{display:block;color:var(--muted);font-size:.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kv{display:grid;grid-template-columns:150px 1fr;gap:.35rem .8rem;font-size:.9rem}
.kv dt{color:var(--muted)} .kv dd{margin:0;word-break:break-word}
.timeline{display:flex;flex-direction:column;gap:.45rem;margin:.4rem 0 0;padding:0;list-style:none}
.timeline li{border-left:2px solid var(--line);padding-left:.7rem;font-size:.87rem}
.timeline li span{color:var(--muted);font-size:.78rem}
.steps{margin:.4rem 0 0;padding-left:1.1rem;font-size:.88rem}
.warnline{color:var(--warn);font-size:.85rem}
.embed{border-left:4px solid var(--accent);background:var(--panel2);border-radius:8px;padding:.7rem .9rem;margin-bottom:.6rem}
.embed b{display:block;margin-bottom:.25rem}
.embed .fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.35rem .8rem;margin-top:.5rem;font-size:.85rem}
.embed .fields i{font-style:normal;color:var(--muted);display:block}
.embed img{max-width:220px;border-radius:8px;margin-top:.5rem;display:block}
.btns{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.5rem}
.bar{height:9px;border-radius:99px;background:#2a2a32;overflow:hidden;margin:.35rem 0 .6rem}
.bar i{display:block;height:100%;background:var(--accent2)}
.bar i.warn{background:var(--warn)} .bar i.ok{background:var(--ok)}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1rem}
textarea.msg{min-height:130px;width:100%;resize:vertical;font-family:inherit}
.picks{max-height:230px;overflow:auto;border:1px solid var(--line);border-radius:10px;padding:.5rem;display:flex;flex-direction:column;gap:.2rem}
.picks label{display:flex;gap:.5rem;align-items:center;font-size:.86rem;color:var(--text);cursor:pointer}
.diff .was{color:var(--muted);font-size:.78rem}
.diff del{text-decoration:none;color:#ff8a80} .diff ins{text-decoration:none;color:#8be0a8}

dialog{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:var(--radius);padding:1.3rem;max-width:520px;width:calc(100% - 2rem)}
dialog::backdrop{background:rgba(0,0,0,.65)}
#toasts{position:fixed;right:1rem;bottom:1rem;display:flex;flex-direction:column;gap:.5rem;z-index:50}
.toast{background:var(--panel2);border:1px solid var(--line);border-left:4px solid var(--accent2);border-radius:9px;padding:.65rem .9rem;max-width:340px;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.toast.ok{border-left-color:var(--ok)} .toast.err{border-left-color:var(--err)}

@media (max-width:820px){
  .shell{grid-template-columns:1fr}
  .side{position:static;height:auto;flex-direction:row;align-items:center;overflow-x:auto;padding:.6rem;gap:.4rem;border-right:0;border-bottom:1px solid var(--line)}
  .brand{display:none} .nav{flex-direction:row} .spacer{display:none}
  .nav button{white-space:nowrap}
  .split{grid-template-columns:1fr}
  .files{flex-direction:row;overflow-x:auto}
  .files button{flex:none}
  .editor{min-height:50vh}
}
</style>
</head>
<body>
<div id="app"></div>
<dialog id="dlg"></dialog>
<div id="toasts"></div>
<script nonce="__NONCE__">
(() => {
'use strict';
const $app = document.getElementById('app');
const state = { page: 'overview', timer: null, isDirty: () => false, botName: 'Бот' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------- мелкие помощники ---------- */
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'value') el.value = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

function toast(msg, kind) {
  const t = h('div', { class: 'toast ' + (kind || '') }, msg);
  document.getElementById('toasts').append(t);
  setTimeout(() => t.remove(), kind === 'err' ? 7000 : 3500);
}

function confirmBox(title, text, okLabel, danger) {
  return new Promise((resolve) => {
    const dlg = document.getElementById('dlg');
    dlg.replaceChildren(
      h('h3', null, title),
      h('p', { class: 'muted' }, text),
      h('div', { class: 'row end' },
        h('button', { class: 'btn', onclick: () => { dlg.close(); resolve(false); } }, 'Отмена'),
        h('button', { class: 'btn ' + (danger ? 'danger' : 'primary'), onclick: () => { dlg.close(); resolve(true); } }, okLabel)
      )
    );
    dlg.addEventListener('close', () => resolve(false), { once: true });
    dlg.showModal();
  });
}

async function api(path, opts = {}) {
  const init = { method: opts.method || 'GET', credentials: 'same-origin', cache: 'no-store', headers: { 'X-Requested-With': 'hz-panel' } };
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  let res;
  try { res = await fetch(path, init); } catch (e) { throw new Error('Нет связи с панелью'); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* пустой ответ */ }
  if (res.status === 401 && path !== '/api/login') { showLogin('Сессия истекла, войдите снова'); throw new Error('Нужен вход'); }
  if (!res.ok) throw new Error((data && data.error) || ('Ошибка ' + res.status));
  return data;
}

function stopTimer() { if (state.timer) { clearInterval(state.timer); state.timer = null; } }

function fmtUptime(s) {
  s = Math.max(0, Math.floor(s));
  const d = Math.floor(s / 86400), hh = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return d + ' д ' + hh + ' ч';
  if (hh) return hh + ' ч ' + m + ' мин';
  return m + ' мин';
}

/* ---------- вход ---------- */
function showLogin(msg) {
  stopTimer();
  state.isDirty = () => false;
  const input = h('input', { type: 'password', placeholder: 'Пароль', autocomplete: 'current-password' });
  const err = h('div', { class: 'err' }, msg || '');
  const form = h('form', {
    class: 'card login',
    onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = '';
      try {
        await api('/api/login', { method: 'POST', body: { password: input.value } });
        await boot();
      } catch (ex) { err.textContent = ex.message; input.select(); }
    }
  },
    h('div', { class: 'logo' }, 'H'),
    h('h1', null, 'Панель управления'),
    h('p', { class: 'muted' }, 'Введите пароль, чтобы продолжить'),
    input,
    h('button', { class: 'btn primary wide', type: 'submit' }, 'Войти'),
    err
  );
  $app.replaceChildren(h('div', { class: 'center' }, form));
  input.focus();
}

/* ---------- каркас ---------- */
const PAGES = [
  ['overview', 'Обзор'],
  ['apps', 'Заявки'],
  ['send', 'Сообщения'],
  ['screens', 'Экраны'],
  ['diag', 'Диагностика'],
  ['audit', 'Ревизия'],
  ['shots', 'Снимки'],
  ['modules', 'Модули'],
  ['settings', 'Настройки'],
  ['logs', 'Логи'],
  ['control', 'Управление'],
];

function renderShell() {
  const nav = h('div', { class: 'nav' }, PAGES.map(([id, label]) =>
    h('button', { 'data-page': id, onclick: () => go(id) }, label)));
  const side = h('aside', { class: 'side' },
    h('div', { class: 'brand' }, h('div', { class: 'logo' }, 'H'), h('div', null, h('b', null, state.botName), h('span', null, 'Панель управления'))),
    nav,
    h('div', { class: 'spacer' }),
    h('button', { class: 'btn', onclick: logout }, 'Выйти')
  );
  $app.replaceChildren(h('div', { class: 'shell' }, side, h('main', { class: 'main', id: 'content' })));
}

async function logout() {
  if (state.isDirty() && !(await confirmBox('Выйти?', 'Есть несохранённые изменения, они пропадут.', 'Выйти', true))) return;
  try { await api('/api/logout', { method: 'POST', body: {} }); } catch (e) { /* уже вышли */ }
  showLogin();
}

async function go(page) {
  if (state.isDirty() && !(await confirmBox('Уйти со страницы?', 'Есть несохранённые изменения, они пропадут.', 'Уйти', true))) return;
  stopTimer();
  state.isDirty = () => false;
  if (!PAGES.some(([id]) => id === page)) page = 'overview';
  state.page = page;
  history.replaceState(null, '', '#' + page);
  document.querySelectorAll('.nav button').forEach((b) => b.classList.toggle('active', b.dataset.page === page));
  const content = document.getElementById('content');
  content.replaceChildren(h('p', { class: 'muted' }, 'Загрузка…'));
  try {
    const view = await ({ overview: pageOverview, apps: pageApps, send: pageSend, screens: pageScreens,
      diag: pageDiag, audit: pageAudit, shots: pageShots,
      modules: pageModules, settings: pageSettings, logs: pageLogs, control: pageControl })[page]();
    if (state.page === page) content.replaceChildren(view);
  } catch (e) {
    if (state.page === page) content.replaceChildren(h('p', { class: 'err' }, e.message));
  }
}

function pageHead(title, sub, ...actions) {
  return h('div', { class: 'head' }, h('div', null, h('h1', null, title), sub ? h('p', { class: 'muted' }, sub) : null), h('div', { class: 'row' }, actions));
}

/* ---------- Обзор ---------- */
const DB_LABELS = {
  pending_apps: 'Заявок на рассмотрении',
  accepted_apps: 'Принято заявок',
  pending_promos: 'Повышений ждут',
  active_warns: 'Активных варнов',
  total_members: 'Участников в базе',
};

async function pageOverview() {
  const d = await api('/api/overview');
  const stat = (label, value, kind) => h('div', { class: 'card stat ' + (kind || '') }, h('small', null, label), h('b', null, value));
  const stats = h('div', { class: 'stats' },
    stat('Статус', d.ready ? 'В сети' : 'Запускается', d.ready ? 'ok' : 'warn'),
    stat('Пинг', d.latency_ms == null ? '—' : d.latency_ms + ' мс'),
    stat('Работает', fmtUptime(d.uptime_s)),
    stat('Память', d.memory_mb == null ? '—' : d.memory_mb + ' МБ'),
    stat('Серверов', d.guilds.length),
    stat('Модулей', d.modules_loaded + ' / ' + d.modules_total)
  );
  const dbKeys = Object.keys(d.stats || {});
  const db = dbKeys.length ? [
    h('div', { class: 'section-title' }, 'База данных'),
    h('div', { class: 'stats' }, dbKeys.map((k) => stat(DB_LABELS[k] || k, d.stats[k])))
  ] : null;
  const guilds = h('div', { class: 'guilds' }, d.guilds.map((g) => {
    const okIcon = typeof g.icon === 'string' && g.icon.startsWith('https://cdn.discordapp.com/');
    return h('div', { class: 'card guild' },
      okIcon ? h('img', { src: g.icon, alt: '', referrerpolicy: 'no-referrer' }) : h('div', { class: 'ava' }, (g.name || '?').slice(0, 1).toUpperCase()),
      h('div', { style: null }, h('b', null, g.name), h('span', { class: 'muted small' }, (g.members == null ? '?' : g.members) + ' участников')));
  }));
  return h('div', null,
    pageHead(d.bot_name || 'Бот', d.user ? d.user + (d.user_id ? ' · ID ' + d.user_id : '') : 'Не авторизован в Discord',
      h('button', { class: 'btn', onclick: () => go('overview') }, 'Обновить')),
    stats, db,
    h('div', { class: 'section-title' }, 'Серверы'),
    d.guilds.length ? guilds : h('p', { class: 'muted' }, 'Бот пока ни на одном сервере.')
  );
}

/* ---------- Модули ---------- */
async function pageModules() {
  let data = await api('/api/modules');
  const search = h('input', { type: 'search', placeholder: 'Поиск модуля…' });
  const list = h('div', { class: 'list' });
  const internal = h('div', { class: 'list' });
  let busy = false;

  async function act(m, action) {
    if (busy) return;
    busy = true;
    try {
      const r = await api('/api/modules/' + encodeURIComponent(m.name) + '/' + action, { method: 'POST', body: {} });
      toast(r.message || 'Готово', 'ok');
    } catch (e) { toast(e.message, 'err'); }
    try { data = await api('/api/modules'); } catch (e) { /* оставим старый список */ }
    busy = false;
    render();
  }

  function chip(m) {
    if (m.loaded) return h('span', { class: 'chip ok' }, 'работает');
    if (m.disabled) return h('span', { class: 'chip off' }, 'отключён');
    return h('span', { class: 'chip err' }, 'не загружен');
  }

  function row(m) {
    const enabled = !m.disabled;
    return h('div', { class: 'card item' },
      h('div', { class: 'grow' }, h('b', null, m.name.replace(/^cogs\./, ''), chip(m), m.protected ? h('span', { class: 'chip' }, 'системный') : null), h('span', { class: 'muted small' }, m.file)),
      h('button', { class: 'btn', disabled: m.protected || (!m.loaded && enabled === false), onclick: () => act(m, 'reload') }, m.loaded ? 'Перезагрузить' : 'Запустить'),
      h('button', { class: 'switch' + (enabled ? ' on' : ''), role: 'switch', 'aria-checked': String(enabled), 'aria-label': 'Включить или отключить ' + m.name, disabled: m.protected, onclick: () => act(m, enabled ? 'disable' : 'enable') })
    );
  }

  function render() {
    const q = search.value.trim().toLowerCase();
    const match = (m) => !q || m.name.toLowerCase().includes(q);
    list.replaceChildren(...data.modules.filter((m) => !m.internal && match(m)).map(row));
    internal.replaceChildren(...data.modules.filter((m) => m.internal && match(m)).map((m) =>
      h('div', { class: 'card item dim' }, h('div', { class: 'grow' }, h('b', null, m.file), h('span', { class: 'muted small' }, 'служебный файл, автоматически не загружается')))));
  }
  search.addEventListener('input', render);
  render();

  const loaded = data.modules.filter((m) => m.loaded).length;
  const total = data.modules.filter((m) => !m.internal).length;
  return h('div', null,
    pageHead('Модули', 'Работает ' + loaded + ' из ' + total + '. Отключённый модуль не запустится и после перезапуска бота.'),
    h('div', { class: 'note' }, 'Модули связаны между собой: например, отключив один из них, вы можете сломать кнопки или команды другого. Если что-то пошло не так, включите модуль обратно. Если после отключения модуль всё равно что-то делает в фоне, перезапустите бота на вкладке «Управление».'),
    h('div', { class: 'toolbar' }, search, h('button', { class: 'btn', onclick: () => go('modules') }, 'Обновить')),
    list,
    data.modules.some((m) => m.internal) ? [h('div', { class: 'section-title' }, 'Служебные файлы'), internal] : null
  );
}

/* ---------- Настройки ---------- */
async function pageSettings() {
  const files = (await api('/api/files')).files;
  if (!files.length) return h('p', { class: 'muted' }, 'JSON-файлов с настройками не найдено.');
  let current = null, original = '', dirty = false;
  const area = h('textarea', { class: 'editor', spellcheck: 'false', wrap: 'off', disabled: true });
  const err = h('div', { class: 'err' });
  const title = h('h2', null, 'Выберите файл');
  const saveBtn = h('button', { class: 'btn primary', disabled: true, onclick: () => save() }, 'Сохранить');
  const resetBtn = h('button', { class: 'btn', disabled: true, onclick: () => { area.value = original; markDirty(); err.textContent = ''; } }, 'Отменить правки');
  const buttons = new Map();

  state.isDirty = () => dirty;
  function markDirty() {
    dirty = current !== null && area.value !== original;
    saveBtn.disabled = !dirty;
    resetBtn.disabled = !dirty;
    title.textContent = current ? current + (dirty ? ' •' : '') : 'Выберите файл';
  }
  area.addEventListener('input', markDirty);
  area.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const s = area.selectionStart, t = area.selectionEnd;
      area.setRangeText('  ', s, t, 'end');
      markDirty();
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      if (dirty) save();
    }
  });

  async function open(name) {
    if (name === current) return;
    if (dirty && !(await confirmBox('Несохранённые правки', 'Если открыть другой файл, правки в «' + current + '» пропадут.', 'Открыть', true))) return;
    try {
      const d = await api('/api/files/' + encodeURIComponent(name));
      current = name; original = d.content;
      area.value = d.content; area.disabled = false; err.textContent = '';
      buttons.forEach((b, n) => b.classList.toggle('active', n === name));
      markDirty();
    } catch (e) { toast(e.message, 'err'); }
  }

  async function save() {
    if (!current) return;
    err.textContent = '';
    try {
      await api('/api/files/' + encodeURIComponent(current), { method: 'PUT', body: { content: area.value } });
      original = area.value; markDirty();
      toast('Сохранено. Прежняя версия лежит рядом: ' + current + '.bak', 'ok');
    } catch (e) { err.textContent = e.message; toast('Не сохранено', 'err'); }
  }

  const fileList = h('div', { class: 'files' }, files.map((f) => {
    const b = h('button', { title: f.name, onclick: () => open(f.name) }, f.name);
    buttons.set(f.name, b);
    return b;
  }));

  return h('div', null,
    pageHead('Настройки', 'Редактор файлов настроек бота. Перед сохранением файл проверяется на ошибки JSON.'),
    h('div', { class: 'note' }, 'config.json бот перечитывает при каждом обращении, поэтому изменения применяются сразу. Остальные файлы модули могут держать в памяти: если правка не подхватилась, перезагрузите нужный модуль на вкладке «Модули». Ctrl+S сохраняет.'),
    h('div', { class: 'split' }, fileList,
      h('div', null, h('div', { class: 'row' }, title, h('div', { class: 'spacer' }), resetBtn, saveBtn), area, err))
  );
}

/* ---------- Логи ---------- */
async function pageLogs() {
  const src = h('select', null, h('option', { value: 'console' }, 'Консоль бота'), h('option', { value: 'file' }, 'Файл bot.log'));
  const cnt = h('select', null, ['200', '500', '1000'].map((n) => h('option', { value: n }, n + ' строк')));
  const q = h('input', { type: 'search', placeholder: 'Фильтр по тексту…' });
  const auto = h('input', { type: 'checkbox', checked: true });
  const box = h('div', { class: 'logbox mono' });
  auto.checked = true;
  let loading = false;

  const level = (l) => {
    if (/error|ошибк|❌|traceback|exception|critical/i.test(l)) return 'e';
    if (/warn|⚠|внимание/i.test(l)) return 'w';
    if (/✅|успешно/i.test(l)) return 'o';
    return '';
  };

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const d = await api('/api/logs?source=' + encodeURIComponent(src.value) + '&lines=' + encodeURIComponent(cnt.value) + '&q=' + encodeURIComponent(q.value.trim()));
      const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
      box.replaceChildren(...(d.lines.length ? d.lines.map((l) => h('div', { class: 'ln ' + level(l) }, l)) : [h('div', { class: 'ln' }, 'Пусто')]));
      if (atBottom) box.scrollTop = box.scrollHeight;
    } catch (e) { /* покажем при следующей попытке */ }
    loading = false;
  }
  src.addEventListener('change', load);
  cnt.addEventListener('change', load);
  q.addEventListener('input', () => { clearTimeout(q._t); q._t = setTimeout(load, 300); });
  await load();
  box.scrollTop = box.scrollHeight;
  state.timer = setInterval(() => { if (auto.checked) load(); }, 3000);

  return h('div', null,
    pageHead('Логи', 'Свежие строки внизу. Страница обновляется сама каждые 3 секунды.'),
    h('div', { class: 'toolbar' }, src, cnt, q, h('label', { class: 'row small muted' }, auto, 'Автообновление'), h('button', { class: 'btn', onclick: () => { load().then(() => { box.scrollTop = box.scrollHeight; }); } }, 'Обновить')),
    box
  );
}

/* ---------- Управление ---------- */
async function waitForRestart(text) {
  stopTimer();
  $app.replaceChildren(h('div', { class: 'center' }, h('div', { class: 'card login' }, h('div', { class: 'spinner' }), h('h2', null, text), h('p', { class: 'muted' }, 'Страница обновится сама, как только панель снова ответит.'))));
  await sleep(4000);
  for (let i = 0; i < 45; i++) {
    try { const r = await fetch('/api/session', { cache: 'no-store' }); if (r.ok) { location.reload(); return; } } catch (e) { /* ещё не поднялся */ }
    await sleep(2000);
  }
  $app.replaceChildren(h('div', { class: 'center' }, h('div', { class: 'card login' }, h('h2', null, 'Панель не отвечает'), h('p', { class: 'muted' }, 'Проверьте консоль на хостинге и запустите бота вручную, если он остановился.'))));
}

async function pageControl() {
  const p = await api('/api/presence');
  const status = h('select', null, [['online', 'В сети'], ['idle', 'Не активен'], ['dnd', 'Не беспокоить'], ['invisible', 'Невидимка']].map(([v, l]) => h('option', { value: v }, l)));
  const kind = h('select', null, [['watching', 'Смотрит'], ['playing', 'Играет в'], ['listening', 'Слушает'], ['competing', 'Участвует в']].map(([v, l]) => h('option', { value: v }, l)));
  const text = h('input', { type: 'text', maxlength: '128', placeholder: 'Например: за порядком' });
  status.value = p.status || 'online';
  kind.value = p.kind || 'watching';
  text.value = p.text || '';

  const apply = h('button', { class: 'btn primary', onclick: async () => {
    try {
      await api('/api/presence', { method: 'POST', body: { status: status.value, kind: kind.value, text: text.value } });
      toast('Статус бота обновлён', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  } }, 'Применить');

  const restart = h('button', { class: 'btn', onclick: async () => {
    if (!(await confirmBox('Перезапустить бота?', 'Бот отключится на 10–30 секунд и запустится заново. Панель вернётся сама.', 'Перезапустить', false))) return;
    try { await api('/api/bot/restart', { method: 'POST', body: {} }); } catch (e) { toast(e.message, 'err'); return; }
    waitForRestart('Бот перезапускается…');
  } }, 'Перезапустить бота');

  const stop = h('button', { class: 'btn danger', onclick: async () => {
    if (!(await confirmBox('Выключить бота?', 'Бот и эта панель полностью остановятся. Включить обратно можно только из панели хостинга.', 'Выключить', true))) return;
    try { await api('/api/bot/shutdown', { method: 'POST', body: {} }); } catch (e) { toast(e.message, 'err'); return; }
    stopTimer();
    $app.replaceChildren(h('div', { class: 'center' }, h('div', { class: 'card login' }, h('h2', null, 'Бот выключен'), h('p', { class: 'muted' }, 'Чтобы запустить его снова, нажмите Start или Restart в панели хостинга.'))));
  } }, 'Выключить бота');

  return h('div', null,
    pageHead('Управление', 'Статус в Discord и питание бота.'),
    h('div', { class: 'cols' },
      h('div', { class: 'card form' },
        h('h2', null, 'Статус бота'),
        h('label', null, 'Состояние', status),
        h('label', null, 'Активность', kind),
        h('label', null, 'Текст (пусто, чтобы убрать)', text),
        h('div', { class: 'row' }, apply),
        h('p', { class: 'muted small' }, 'Статус сохраняется и применяется после каждого запуска бота.')
      ),
      h('div', { class: 'card form danger-zone' },
        h('h2', null, 'Питание'),
        h('p', { class: 'muted small' }, 'Перезапуск помогает, если бот завис или вы изменили настройки, которые читаются только при старте.'),
        h('div', { class: 'row' }, restart, stop)
      )
    )
  );
}

/* ---------- общие кусочки новых разделов ---------- */
function spinner(text) { return h('p', { class: 'muted' }, text || 'Загрузка…'); }

function chip(text, kind) { return h('span', { class: 'chip ' + (kind || '') }, text); }

function kv(pairs) {
  const dl = h('dl', { class: 'kv' });
  pairs.filter((p) => p[1] !== null && p[1] !== undefined && p[1] !== '').forEach(([k, v]) => {
    dl.append(h('dt', null, k), h('dd', null, String(v)));
  });
  return dl;
}

function lines(list, cls) {
  const ul = h('ul', { class: cls || 'steps' });
  list.filter(Boolean).forEach((t) => ul.append(h('li', null, String(t))));
  return ul;
}

async function staffBox(label, selected) {
  const sel = h('select', null);
  const people = await api('/api/staff');
  if (!people.length) return h('p', { class: 'warnline' }, 'На сервере никто не числится сотрудником — панели не от кого будет действовать.');
  people.forEach((p) => sel.append(h('option', { value: p.id }, p.name + (p.top_role ? ' · ' + p.top_role : ''))));
  if (selected) sel.value = selected;
  return h('label', null, label, sel);
}

function linkRow(url, text) {
  return h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', class: 'btn' }, text || 'Открыть в Discord');
}

/* ---------- Заявки ---------- */
const APP_STATUS = [['open', 'открытые'], ['', 'все'], ['pending', 'новые'], ['in_review', 'на рассмотрении'],
  ['accepted', 'принятые'], ['rejected', 'отказ']];

async function pageApps() {
  const q = h('input', { type: 'search', placeholder: 'Ник, статика, ID…' });
  const status = h('select', null, APP_STATUS.map(([v, l]) => h('option', { value: v }, l)));
  status.value = 'open';
  const counts = h('div', { class: 'pager' });
  const listBox = h('div', { class: 'rows' });
  const detail = h('div', { class: 'card' }, spinner('Выбери заявку.'));
  let picked = 0;

  function pick(id) {
    picked = id;
    document.querySelectorAll('[data-app]').forEach((b) => b.classList.toggle('sel', +b.dataset.app === id));
    detail.replaceChildren(spinner('Читаю заявку…'));
    api('/api/apps/' + id).then(renderDetail).catch((e) => detail.replaceChildren(h('p', { class: 'err' }, e.message)));
  }

  async function load() {
    let d;
    try { d = await api('/api/apps?status=' + encodeURIComponent(status.value) + '&q=' + encodeURIComponent(q.value.trim())); }
    catch (e) { listBox.replaceChildren(h('p', { class: 'err' }, e.message)); return; }
    counts.replaceChildren(...Object.entries(d.counts || {}).map(([k, v]) => h('span', { class: 'chip' }, k + ': ' + v)));
    listBox.replaceChildren(...(d.apps.length ? d.apps.map((a) => {
      const b = h('button', { class: 'r' + (a.id === picked ? ' sel' : ''), 'data-app': a.id, onclick: () => pick(a.id) },
        h('div', { class: 'grow' }, h('b', null, a.nick + ' · ' + (a.static_id || 'без статики')),
          h('small', null, '#' + a.id + ' · ' + a.status_label + ' · ' + (a.created_at || '').slice(0, 16).replace('T', ' '))),
        chip(a.closed ? 'закрыта' : a.status_label, a.closed ? 'off' : 'ok'));
      return b;
    }) : [h('p', { class: 'muted' }, 'Ничего не нашлось.')]));
  }

  function answersNode(a) {
    const out = [];
    (function walk(node, prefix) {
      if (node === null || node === undefined) return;
      if (Array.isArray(node)) { node.forEach((v, i) => walk(v, prefix + ' ' + (i + 1))); return; }
      if (typeof node === 'object') { Object.entries(node).forEach(([k, v]) => walk(v, prefix.length ? prefix + ' · ' + k : k)); return; }
      out.push([prefix || 'ответ', String(node)]);
    })(a, '');
    return out.length ? kv(out) : h('p', { class: 'muted small' }, 'Ответов в базе нет.');
  }

  async function renderDetail(d) {
    detail.replaceChildren();
    const head = h('div', { class: 'row' }, h('h2', null, '#' + d.id + ' ' + d.nick),
      chip(d.status_label, d.closed ? 'off' : 'ok'), d.repeat ? chip('повтор') : null,
      d.in_family === true ? chip('в составе') : null, d.link ? linkRow(d.link) : null);
    detail.append(head, kv([
      ['Статика', d.static_id], ['Возраст', d.age], ['ID', d.user_id], ['Заявка', (d.created_at || '').replace('T', ' ')],
      ['Рекрутер', d.reviewer], ['Раньше был в', d.prev_families], ['Почему к нам', d.why],
      ['Причина отказа', d.reason], ['Ранг в семье', d.member_rank],
    ]));
    detail.append(h('div', { class: 'section-title' }, 'Анкета'), answersNode(d.answers));
    detail.append(h('div', { class: 'section-title' }, 'История'),
      d.history && d.history.length ? h('ul', { class: 'timeline' }, d.history.map((x) =>
        h('li', null, x.label, ' · ', String(x.actor || 'discord'), h('span', null, ' ' + (x.at || '').slice(0, 16).replace('T', ' ')),
          x.note ? h('span', null, ' — ' + x.note) : null))) : h('p', { class: 'muted small' }, 'История пуста.'));

    if (d.closed) { detail.append(h('p', { class: 'muted small' }, 'Заявка закрыта — менять уже нечего.')); return; }

    const staff = await staffBox('Действует как', null);
    const voices = await api('/api/voices').catch(() => []);
    const voice = voices.length ? h('label', null, 'Куда звать на обзвон',
      h('select', null, voices.map((v) => h('option', { value: v.id }, v.name)))) : null;
    const reason = h('input', { type: 'text', maxlength: '500', placeholder: 'Причина отказа (уходит кандидату в ЛС)' });
    const out = h('div');
    const act = async (action) => {
      out.replaceChildren(spinner('Делаю…'));
      const body = { action, actor: staff.querySelector('select').value, reason: reason.value.trim() };
      if (voice) body.voice = voice.querySelector('select').value;
      try {
        const r = await api('/api/apps/' + d.id + '/act', { method: 'POST', body });
        out.replaceChildren(h('div', { class: 'section-title' }, 'Что произошло'), lines(r.steps),
          r.warnings && r.warnings.length ? h('p', { class: 'warnline' }, r.warnings.join(' · ')) : null);
        load();
      } catch (e) { out.replaceChildren(h('p', { class: 'err' }, e.message)); }
    };
    detail.append(h('div', { class: 'section-title' }, 'Действие'),
      h('div', { class: 'form' }, staff, voice, reason,
        h('div', { class: 'btns' },
          h('button', { class: 'btn', onclick: () => act('review') }, 'Взять'),
          h('button', { class: 'btn', onclick: () => act('interview') }, 'На обзвон'),
          h('button', { class: 'btn primary', onclick: () => act('accept') }, 'Принять'),
          h('button', { class: 'btn danger', onclick: () => act('reject') }, 'Отказать')),
        out));
  }

  q.addEventListener('input', () => { clearTimeout(q._t); q._t = setTimeout(load, 300); });
  status.addEventListener('change', load);
  await load();
  return h('div', null,
    pageHead('Заявки', 'Анкеты, история каждой заявки и те же действия, что делает карточка в Discord.',
      h('button', { class: 'btn', onclick: load }, 'Обновить')),
    h('div', { class: 'toolbar' }, q, status), counts,
    h('div', { class: 'split', style: 'grid-template-columns:minmax(260px,340px) 1fr;margin-top:.9rem' }, listBox, detail)
  );
}

/* ---------- Сообщения ---------- */
function msgForm(needPing) {
  const title = h('input', { type: 'text', maxlength: '256', placeholder: 'Заголовок (пусто — обычный текст)' });
  const color = h('input', { type: 'text', maxlength: '9', placeholder: 'Цвет, например 990000', value: '990000' });
  const text = h('textarea', { class: 'msg', placeholder: 'Текст сообщения…' });
  const ping = h('input', { type: 'checkbox' });
  const box = h('div', { class: 'form' }, title, color, text);
  if (needPing) box.append(h('label', { class: 'row small' }, ping, 'Отметить @everyone'));
  return { box, title, color, text, ping };
}

function resultBox() {
  const box = h('div');
  box.show = (r) => box.replaceChildren(
    r.link ? linkRow(r.link, 'Сообщение в Discord') : null,
    r.where ? chip(r.where) : null,
    r.sent !== undefined ? h('p', null, 'Дошло: ' + r.sent + ', не дошло: ' + r.failed_total) : null,
    r.failed && r.failed.length ? lines(r.failed, 'steps') : null,
    r.hint ? h('p', { class: 'warnline small' }, r.hint) : null,
  );
  box.err = (e) => box.replaceChildren(h('p', { class: 'err' }, e.message));
  box.busy = () => box.replaceChildren(spinner('Отправляю…'));
  return box;
}

async function pageSend() {
  const cq = h('input', { type: 'search', placeholder: 'Имя канала…' });
  const chBox = h('div', { class: 'picks' });
  let channelId = '';
  async function loadChannels() {
    const d = await api('/api/channels?q=' + encodeURIComponent(cq.value.trim()));
    chBox.replaceChildren(...d.rows.map((c) => h('label', null,
      h('input', { type: 'radio', name: 'chan', value: c.id, checked: c.id === channelId, onchange: () => { channelId = c.id; } }),
      h('span', null,
        (c.kind === 'ветка' ? '🧵 ' : '#') + c.name + (c.can_write === false ? ' (нет прав)' : ''),
        c.is_review ? chip('канал заявок', 'err') : null)
    )));
    if (!d.rows.length) chBox.append(h('p', { class: 'muted small' }, 'Не нашлось ни одного канала.'));
  }

  const mq = h('input', { type: 'search', placeholder: 'Ник или ID…' });
  const mBox = h('div', { class: 'picks' });
  let dmUser = '';
  const chosen = new Set();
  async function loadMembers() {
    const d = await api('/api/members?q=' + encodeURIComponent(mq.value.trim()) + '&limit=60');
    mBox.replaceChildren(...d.rows.map((m) => {
      const one = h('label', null, h('span', null, m.name + (m.top ? ' · ' + m.top : '') + (m.voice ? ' · 🎙 ' + m.voice : '')));
      const radio = h('input', { type: 'radio', name: 'dmuser', value: m.id, checked: m.id === dmUser, onchange: () => { dmUser = m.id; } });
      const check = h('input', { type: 'checkbox', value: m.id, onchange: (e) => { e.target.checked ? chosen.add(m.id) : chosen.delete(m.id); } });
      one.prepend(radio, check);
      return one;
    }));
    if (!d.rows.length) mBox.append(h('p', { class: 'muted small' }, 'Никого не нашлось.'));
  }

  const f1 = msgForm(true), r1 = resultBox();
  f1.box.append(h('button', { class: 'btn primary', onclick: async () => {
    r1.busy();
    try { r1.show(await api('/api/send', { method: 'POST', body: { channel: channelId, text: f1.text.value, title: f1.title.value, color: f1.color.value, ping: f1.ping.checked } })); }
    catch (e) { r1.err(e); }
  } }, 'Отправить в канал'), h('p', { class: 'muted small' }, 'Сначала выбери канал слева.'));

  const f2 = msgForm(false), r2 = resultBox();
  f2.box.append(h('button', { class: 'btn primary', onclick: async () => {
    r2.busy();
    try { r2.show(await api('/api/dm', { method: 'POST', body: { user: dmUser, text: f2.text.value, title: f2.title.value, color: f2.color.value } })); }
    catch (e) { r2.err(e); }
  } }, 'Отправить в ЛС'));

  const f3 = msgForm(false), r3 = resultBox();
  const mark = h('small', { class: 'muted' }, 'Отмечено: 0');
  const upd = () => { mark.textContent = 'Отмечено: ' + chosen.size; };
  f3.box.append(h('div', { class: 'row' }, h('button', { class: 'btn', onclick: async () => {
    upd();
    r3.busy();
    try { r3.show(await api('/api/broadcast', { method: 'POST', body: { users: [...chosen], text: f3.text.value, title: f3.title.value, color: f3.color.value, dry: true } })); }
    catch (e) { r3.err(e); }
  } }, 'Проверить без отправки'),
  h('button', { class: 'btn danger', onclick: async () => {
    if (!(await confirmBox('Рассылка в ЛС?', 'Сообщение уйдёт ' + chosen.size + ' людям. Отменить отправку нельзя.', 'Разослать', true))) return;
    r3.busy();
    try { r3.show(await api('/api/broadcast', { method: 'POST', body: { users: [...chosen], text: f3.text.value, title: f3.title.value, color: f3.color.value, dry: false } })); }
    catch (e) { r3.err(e); }
  } }, 'Разослать'), mark));

  cq.addEventListener('input', () => { clearTimeout(cq._t); cq._t = setTimeout(loadChannels, 300); });
  mq.addEventListener('input', () => { clearTimeout(mq._t); mq._t = setTimeout(loadMembers, 300); });
  await Promise.all([loadChannels(), loadMembers()]);

  return h('div', null,
    pageHead('Сообщения', 'Панель пишет от имени бота: в канал, в ЛС и рассылкой по составу.'),
    h('div', { class: 'grid2' },
      h('div', { class: 'card form' }, h('h2', null, 'В канал'),
        h('label', null, 'Поиск канала', cq), chBox, f1.box, r1),
      h('div', { class: 'card form' }, h('h2', null, 'В ЛС'),
        h('label', null, 'Кого', mq), mBox, f2.box, r2,
        h('div', { class: 'section-title' }, 'Рассылка'), f3.box, r3)
    )
  );
}

/* ---------- Экраны команд ---------- */
function embedNode(e) {
  const box = h('div', { class: 'embed', style: 'border-left-color:' + (e.color || '#990000') });
  if (e.author) box.append(h('small', { class: 'muted' }, e.author));
  if (e.title) box.append(h('b', null, e.title));
  if (e.text) box.append(h('div', null, e.text));
  if (e.fields && e.fields.length) box.append(h('div', { class: 'fields' },
    e.fields.map((f) => h('div', null, h('i', null, f.name), f.value))));
  if (e.image) box.append(h('img', { src: e.image, alt: '', referrerpolicy: 'no-referrer' }));
  if (e.footer) box.append(h('small', { class: 'muted' }, e.footer));
  return box;
}

function screenNode(res, actorSel, refresh) {
  const box = h('div');
  if (res.kind === 'deleted') box.append(h('p', { class: 'muted' }, 'Ответ удалён.'));
  if (res.content) box.append(h('div', { class: 'card small mono' }, res.content));
  (res.embeds || []).forEach((e) => box.append(embedNode(e)));
  (res.extra || []).forEach((x) => box.append(h('div', { class: 'card small' }, String(x.content || ''))));
  if (res.hint) box.append(h('p', { class: 'warnline small' }, res.hint));

  const post = async (path, body, busy) => {
    if (busy) busy.replaceChildren(spinner('Думаю…'));
    try { refresh(await api(path, { method: 'POST', body })); }
    catch (e) { toast(e.message, 'err'); }
  };

  if (res.token && (res.components || []).length) {
    const pick = {};
    const ctr = h('div', { class: 'btns' });
    res.components.forEach((c) => {
      if (c.kind === 'button') {
        ctr.append(h('button', { class: 'btn', disabled: c.disabled, onclick: () =>
          post('/api/commands/press', { token: res.token, index: c.index, actor: actorSel.value }, ctr) },
          (c.emoji ? c.emoji + ' ' : '') + (c.label || 'кнопка')));
      } else if (c.kind === 'select') {
        const sel = h('select', null, (c.options || []).map((o) => h('option', { value: o.value }, (o.emoji ? o.emoji + ' ' : '') + o.label)));
        pick[c.index] = sel;
        ctr.append(h('div', { class: 'row' }, h('small', { class: 'muted' }, c.label), sel,
          h('button', { class: 'btn', onclick: () =>
            post('/api/commands/press', { token: res.token, index: c.index, actor: actorSel.value, values: [sel.value] }, ctr) }, 'Выбрать')));
      } else if (c.kind === 'input') {
        ctr.append(h('p', { class: 'muted small' }, 'Поле «' + (c.label || '') + '» — его заполняют в форме ниже.'));
      }
    });
    box.append(h('div', { class: 'section-title' }, 'Кнопки экрана'), ctr);
  }

  if (res.modal) {
    const m = res.modal, inputs = {};
    const form = h('div', { class: 'form' }, ...m.fields.map((f) => {
      const el = f.multiline ? h('textarea', { class: 'msg', maxlength: String(f.max) }) : h('input', { type: 'text', maxlength: String(f.max) });
      inputs[f.id] = el;
      return h('label', null, f.label || f.id, el);
    }));
    form.append(h('button', { class: 'btn primary', onclick: (ev) => {
      const fields = {};
      Object.entries(inputs).forEach(([k, el]) => { fields[k] = el.value; });
      post('/api/commands/modal', { token: m.token, actor: actorSel.value, fields }, form);
    } }, 'Отправить форму'));
    box.append(h('div', { class: 'section-title' }, m.title || 'Форма'), form);
  }
  return box;
}

async function pageScreens() {
  const d = await api('/api/commands').catch((e) => ({ error: e.message }));
  if (d.error) return h('div', null, pageHead('Экраны', null), h('p', { class: 'err' }, d.error));
  const staff = await api('/api/staff');
  const actorSel = h('select', null, staff.map((p) => h('option', { value: p.id }, p.name)));
  const chans = await api('/api/channels');
  const chanSel = h('select', null, chans.rows.filter((c) => c.can_write !== false)
    .map((c) => h('option', { value: c.id }, (c.kind === 'ветка' ? '🧵 ' : '#') + c.name)));
  const out = h('div', { class: 'card' }, h('p', { class: 'muted' }, 'Открой экран — его ответ появится здесь, как в Discord.'));
  const refresh = (res) => out.replaceChildren(screenNode(res, actorSel, refresh));

  const nav = h('div', { class: 'grid2' }, d.sections.map((s) => h('div', { class: 'card form' },
    h('h2', null, s.icon + ' ' + s.title),
    ...s.screens.map((x) => h('button', { class: 'btn', onclick: async () => {
      out.replaceChildren(spinner('Экран думает…'));
      try { refresh(await api('/api/commands/run', { method: 'POST', body: { ref: x.ref, actor: actorSel.value, channel: chanSel.value } })); }
      catch (e) { out.replaceChildren(h('p', { class: 'err' }, e.message)); }
    } }, (s.icon || '') + ' ' + x.label + ' · ' + x.cmd)))));

  return h('div', null,
    pageHead('Экраны команд', 'Команды бота работают отсюда: экран получает тот же ответ, что и в Discord, и его кнопки живые.',
      h('button', { class: 'btn', onclick: () => go('screens') }, 'Обновить')),
    h('div', { class: 'toolbar' }, h('label', null, 'От чьего имени', actorSel), h('label', null, 'Куда отвечать', chanSel),
      d.hidden && d.hidden.length ? chip('скрытых дублей: ' + d.hidden.length, 'off') : null),
    nav, h('div', { class: 'section-title' }, 'Ответ'), out
  );
}

/* ---------- Диагностика ---------- */
function bar(pct) {
  return h('div', { class: 'bar' }, h('i', { class: pct >= 85 ? '' : pct >= 70 ? 'warn' : 'ok', style: 'width:' + Math.min(100, pct) + '%' }));
}

async function pageDiag() {
  const sys = await api('/api/diag/system').catch((e) => ({ error: e.message }));
  const sysBox = h('div', { class: 'card form' });
  if (sys.error) sysBox.append(h('p', { class: 'err' }, sys.error));
  else {
    const m = sys.memory;
    sysBox.append(h('h2', null, 'Память'),
      h('div', null, h('b', null, m.container + ' / ' + m.limit + ' МБ контейнера'), bar(m.percent),
        h('small', { class: 'muted' }, m.percent + '% · ' + m.verdict)),
      kv([['Бот', m.bot + ' МБ'], ['Пик бота', m.peak + ' МБ'], ['Свободно', m.free + ' МБ'],
        ['Кэш-файлы', m.page_cache + ' МБ'], ['Аптайм', sys.uptime], ['Пинг', sys.latency_ms + ' мс'],
        ['Серверов', sys.guilds], ['Ког', sys.cogs_on.length]]),
      h('div', { class: 'section-title' }, 'Процессы'),
      lines(sys.processes.map((p) => p.name + ' · ' + p.pid + ' · ' + p.mb + ' МБ')),
      h('div', { class: 'section-title' }, 'Кэш disnake'),
      kv([['Участников', sys.cache.members], ['Сообщений', sys.cache.messages]]),
      sys.children && sys.children.length ? h('p', { class: 'muted small' }, 'Дочерние процессы: ' + sys.children.length) : null);
  }

  const hours = h('select', null, [['1', 'за час'], ['24', 'за сутки'], ['72', 'за 3 дня'], ['168', 'за неделю']]
    .map(([v, l]) => h('option', { value: v }, l)));
  hours.value = '24';
  const dayBox = h('div', { class: 'card form' });
  async function loadDay() {
    dayBox.replaceChildren(spinner());
    const d = await api('/api/diag/day?hours=' + hours.value);
    dayBox.replaceChildren(h('h2', null, 'Что произошло'), h('div', { class: 'pager' },
      ...Object.entries(d.counts || {}).map(([k, v]) => h('span', { class: 'chip' }, k + ': ' + v)),
      ...Object.entries(d.totals || {}).map(([k, v]) => h('span', { class: 'chip ok' }, k + ': ' + v))),
      h('div', { class: 'rows' }, d.rows.map((r) => h('div', { class: 'r' },
        h('div', { class: 'grow' }, h('b', null, r.what + ' · ' + (r.who || '—')), h('small', null, String(r.state || ''))),
        h('small', { class: 'muted' }, String(r.at || '').slice(0, 16).replace('T', ' '))))));
  }

  const days = h('select', null, [['7', 'за 7 дней'], ['30', 'за 30 дней'], ['90', 'за 90 дней']].map(([v, l]) => h('option', { value: v }, l)));
  const silBox = h('div', { class: 'card form' });
  async function loadSil() {
    silBox.replaceChildren(spinner('Считаем голосовое…'));
    let d;
    try { d = await api('/api/diag/silents?days=' + days.value); } catch (e) { silBox.replaceChildren(h('p', { class: 'err' }, e.message)); return; }
    silBox.replaceChildren(h('h2', null, 'Молчуны по голосовому'),
      h('p', { class: 'muted small' }, 'Проверено ' + d.checked + ' человек, срок — ' + d.period + '. Без голоса и без отпуска.'),
      h('div', { class: 'rows' }, d.silent.length ? d.silent.map((s) => h('div', { class: 'r' },
        h('div', { class: 'grow' }, h('b', null, s.nick + ' · ' + (s.static_id || 'без статики')),
          h('small', null, (s.on_server ? 'на сервере' : 'нет на сервере') + ' · ' + (s.rank || '?') + ' ранг')),
        chip(s.name || 'ЛС не найти'))) : [h('p', { class: 'muted' }, 'Молчунов нет.')]));
  }

  hours.addEventListener('change', loadDay);
  days.addEventListener('change', loadSil);
  await Promise.all([loadDay(), loadSil()]);

  return h('div', null,
    pageHead('Диагностика', 'Хватает ли памяти, что произошло в семье и кто молчит в голосовом.',
      h('button', { class: 'btn', onclick: () => go('diag') }, 'Обновить')),
    h('div', { class: 'grid2' }, sysBox,
      h('div', { class: 'form' }, h('label', null, 'Отчёт', hours), dayBox, h('label', null, 'Молчуны', days), silBox))
  );
}

/* ---------- Ревизия ---------- */
async function pageAudit() {
  const limit = h('select', null, [['20', '20 последних'], ['40', '40 последних'], ['80', '80 последних']].map(([v, l]) => h('option', { value: v }, l)));
  const chBox = h('div', { class: 'card form' });
  async function loadCh() {
    chBox.replaceChildren(spinner());
    let d;
    try { d = await api('/api/audit/channels?limit=' + limit.value); } catch (e) { chBox.replaceChildren(h('p', { class: 'err' }, e.message)); return; }
    chBox.replaceChildren(h('h2', null, 'Служебные каналы'),
      h('div', { class: 'rows' }, d.rows.map((r) => h('div', { class: 'r' },
        h('div', { class: 'grow' }, h('b', null, r.channel + (r.name ? ' · ' + r.name : '')), h('small', null, r.state)),
        r.cards !== undefined ? chip('заявок ' + r.cards + ' / служебного ' + r.service, /раздувает/.test(r.state) ? 'err' : 'ok') : chip('—', 'off')))));
  }

  const emBox = h('div', { class: 'card form' });
  async function loadEm() {
    emBox.replaceChildren(spinner());
    let d;
    try { d = await api('/api/audit/emojis'); } catch (e) { emBox.replaceChildren(h('p', { class: 'err' }, e.message)); return; }
    emBox.replaceChildren(h('h2', null, 'Имена эмодзи'),
      h('div', { class: 'pager' }, h('span', { class: 'chip' }, 'файлов: ' + d.scanned),
        h('span', { class: 'chip' }, 'на сервере: ' + d.server), h('span', { class: 'chip' }, 'упомянуто: ' + d.referenced),
        h('span', { class: 'chip ' + (d.missing_total ? 'err' : 'ok') }, 'выдуманных: ' + d.missing_total),
        h('span', { class: 'chip off' }, 'не используется: ' + d.unused_total)),
      h('p', { class: 'muted small' }, 'Выдуманное имя не роняет бота — эмодзи просто исчезает из текста, и на экране остаётся пустое место.'),
      d.missing.length ? h('div', { class: 'rows' }, d.missing.map((m) => h('div', { class: 'r' },
        h('div', { class: 'grow' }, h('b', null, ':' + m.token + ':'), h('small', null, m.where.join(', '))),
        chip('×' + m.count, 'err')))) : h('p', { class: 'muted' }, 'Все имена живые.'),
      d.unused.length ? h('details', null, h('summary', null, 'Эмодзи сервера, которые нигде не упоминаются'),
        h('p', { class: 'muted small mono' }, d.unused.join(', '))) : null);
  }

  limit.addEventListener('change', loadCh);
  await Promise.all([loadCh(), loadEm()]);
  return h('div', null,
    pageHead('Ревизия', 'Не загазован ли канал заявок и не осталось ли в коде имён эмодзи, которых нет на сервере.',
      h('button', { class: 'btn', onclick: () => go('audit') }, 'Обновить')),
    h('div', { class: 'toolbar' }, h('label', null, 'Глубина', limit)),
    h('div', { class: 'grid2' }, chBox, emBox)
  );
}

/* ---------- Снимки ---------- */
async function pageShots() {
  const d = await api('/api/snapshots').catch((e) => ({ error: e.message }));
  if (d.error) return h('div', null, pageHead('Снимки', null), h('p', { class: 'err' }, d.error));
  const list = h('div', { class: 'rows' });
  const detail = h('div', { class: 'card' }, spinner('Выбери снимок, чтобы увидеть, что вернётся.'));

  async function open(name) {
    document.querySelectorAll('[data-snap]').forEach((b) => b.classList.toggle('sel', b.dataset.snap === name));
    detail.replaceChildren(spinner('Сравниваю с живыми файлами…'));
    let s;
    try { s = await api('/api/snapshots/' + encodeURIComponent(name)); }
    catch (e) { detail.replaceChildren(h('p', { class: 'err' }, e.message)); return; }
    detail.replaceChildren();
    detail.append(h('div', { class: 'row' }, h('h2', null, s.name), chip(s.at || '')),
      h('p', { class: 'muted small' }, s.note),
      h('div', { class: 'section-title' }, 'Что изменится'),
      s.changes.length ? h('div', { class: 'rows diff' }, s.changes.map((c) => h('div', { class: 'r' },
        h('div', { class: 'grow' }, h('b', null, c.path || c.name || ''), h('small', { class: 'was' }, (c.file || '') + ' · было: ' + String(c.was ?? '—'))),
        chip(c.kind, c.kind === 'исчезнет' ? 'err' : c.kind === 'вернётся' ? 'ok' : '')))) : h('p', { class: 'muted' }, 'Отличий нет.'),
      h('div', { class: 'section-title' }, 'Откат'),
      h('button', { class: 'btn danger', onclick: async () => {
        if (!(await confirmBox('Вернуть этот снимок?', 'Файлы настроек лягут как были в ' + (s.at || name) + '. Текущее состояние уйдёт в новый снимок, так что можно вернуть обратно.', 'Откатить', true))) return;
        try {
          const r = await api('/api/snapshots/' + encodeURIComponent(name) + '/restore', { method: 'POST', body: {} });
          detail.append(h('div', { class: 'section-title' }, 'Готово'), lines(r.steps),
            r.warnings && r.warnings.length ? h('p', { class: 'warnline' }, r.warnings.join(' · ')) : null);
          toast('Откат прошёл', 'ok');
        } catch (e) { toast(e.message, 'err'); }
      } }, 'Откатить к этому снимку'));
  }

  list.replaceChildren(...d.rows.map((r) => h('button', { class: 'r', 'data-snap': r.name, onclick: () => open(r.name) },
    h('div', { class: 'grow' }, h('b', null, r.at || r.name), h('small', null, r.name)),
    chip(r.files + ' файлов · ' + r.kb + ' КБ'), r.live ? chip('самый свежий', 'ok') : null)));

  return h('div', null,
    pageHead('Снимки', 'Панель сама снимает состояние настроек. Откат обратим: то, что живёт сейчас, уходит в новый снимок.',
      h('button', { class: 'btn', onclick: () => go('shots') }, 'Обновить')),
    h('p', { class: 'muted small' }, 'Храним ' + d.keep + ' последних снимков, всего папок: ' + d.total + '.'),
    h('div', { class: 'split', style: 'grid-template-columns:minmax(260px,340px) 1fr' }, list, detail)
  );
}

/* ---------- запуск ---------- */
async function boot() {
  let s;
  try {
    const r = await fetch('/api/session', { credentials: 'same-origin', cache: 'no-store' });
    s = await r.json();
  } catch (e) {
    $app.replaceChildren(h('div', { class: 'center' }, h('p', { class: 'muted' }, 'Нет связи с панелью. Обновите страницу.')));
    return;
  }
  if (!s.authed) { showLogin(); return; }
  state.botName = s.bot_name || 'Бот';
  document.title = state.botName + ' · Панель';
  renderShell();
  go(location.hash.slice(1) || 'overview');
}

window.addEventListener('beforeunload', (e) => { if (state.isDirty()) { e.preventDefault(); e.returnValue = ''; } });
boot();
})();
</script>
</body>
</html>
"""
