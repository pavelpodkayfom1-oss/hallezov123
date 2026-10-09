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

dialog{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:var(--radius);padding:1.3rem;max-width:420px;width:calc(100% - 2rem)}
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
    const view = await ({ overview: pageOverview, modules: pageModules, settings: pageSettings, logs: pageLogs, control: pageControl })[page]();
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
