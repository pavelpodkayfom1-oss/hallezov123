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
<meta name="theme-color" content="#07070a">
<style nonce="__NONCE__">
/* ============================================================
   Панель управления — новый дизайн
   Боковое меню, мягкие поверхности, один акцент (красный бренда).
   Все цвета — переменные, светлая тема меняет только их.
   ============================================================ */
:root{
  --bg:#07070a; --bg2:#0b0b10;
  --panel:#101015; --panel2:#16161d; --panel3:#1d1d26;
  --line:rgba(255,255,255,.075); --line2:rgba(255,255,255,.14);
  --text:#f3f3f7; --muted:#8b8b9a; --faint:#5b5b69;
  --accent:#d92d3a; --accent2:#ff5a64; --accent-d:#a8121f;
  --accent-soft:rgba(255,90,100,.12); --accent-line:rgba(255,90,100,.38);
  --ok:#3dd68c; --warn:#f5a524; --err:#ff6369;
  --field:#0c0c11; --hover:rgba(255,255,255,.055); --code:#08080b;
  --radius:16px; --radius-s:11px;
  --glow:0 1px 2px rgba(0,0,0,.4),0 14px 36px rgba(0,0,0,.35);
  --edge:inset 0 1px 0 rgba(255,255,255,.05);
  --ease:cubic-bezier(.2,.7,.3,1);
  --side:268px;
  color-scheme:dark;
}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
html{scrollbar-width:thin;scrollbar-color:#2c2c36 transparent}
body{
  background:var(--bg);color:var(--text);
  font:15px/1.55 "Inter",system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  font-feature-settings:"cv11","ss01";
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
  position:relative;isolation:isolate;min-height:100vh
}
/* две мягкие засветки и тонкая точечная сетка, всё под содержимым */
body::before{content:"";position:fixed;inset:0;z-index:-2;pointer-events:none;
  background:
    radial-gradient(52% 44% at 8% -6%, rgba(217,45,58,.20), transparent 70%),
    radial-gradient(44% 40% at 100% 108%, rgba(120,70,255,.11), transparent 70%),
    linear-gradient(180deg, var(--bg2), var(--bg))}
body::after{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;opacity:.55;
  background-image:radial-gradient(rgba(255,255,255,.07) 1px, transparent 1.2px);
  background-size:26px 26px;
  -webkit-mask-image:linear-gradient(180deg,#000,transparent 70%);mask-image:linear-gradient(180deg,#000,transparent 70%)}
::selection{background:rgba(255,90,100,.35)}
h1,h2,h3{margin:0 0 .4em;font-weight:650;letter-spacing:-.02em;line-height:1.2}
h1{font-size:1.7rem} h2{font-size:1.2rem} h3{font-size:1rem}
p{margin:.3em 0 .9em}
a{color:var(--accent2)}
.muted{color:var(--muted)} .mono{font-family:ui-monospace,"JetBrains Mono",SFMono-Regular,Menlo,Consolas,monospace}
.small{font-size:.85rem}
button,input,select,textarea{font:inherit;color:inherit}

/* ---------- поля ---------- */
input,select,textarea{background:var(--field);border:1px solid var(--line);border-radius:var(--radius-s);
  padding:.62em .85em;min-width:0;color:var(--text);
  transition:border-color .15s,box-shadow .15s,background .15s}
input::placeholder,textarea::placeholder{color:var(--faint)}
input:hover,select:hover,textarea:hover{border-color:var(--line2)}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent-line);box-shadow:0 0 0 4px var(--accent-soft)}
button:focus-visible{outline:2px solid var(--accent2);outline-offset:2px}
select{appearance:none;-webkit-appearance:none;
  background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),linear-gradient(135deg,var(--muted) 50%,transparent 50%);
  background-position:calc(100% - 17px) calc(50% + 2px),calc(100% - 12px) calc(50% + 2px);
  background-size:5px 5px,5px 5px;background-repeat:no-repeat;padding-right:2.2em}
select[size],select[multiple]{background-image:none;padding-right:.8em;min-height:0}
select[size] option{padding:.25em .45em;border-radius:6px}
input[type=checkbox],input[type=radio]{appearance:none;-webkit-appearance:none;width:18px;height:18px;
  min-width:0;flex:none;padding:0;border-radius:6px;border:1.5px solid var(--line2);background:var(--field);
  cursor:pointer;display:grid;place-items:center;transition:border-color .14s,background .14s,box-shadow .14s}
input[type=radio]{border-radius:50%}
input[type=checkbox]:hover,input[type=radio]:hover{border-color:var(--accent2)}
input[type=checkbox]:checked,input[type=radio]:checked{border-color:transparent;
  background:linear-gradient(180deg,var(--accent2),var(--accent));box-shadow:0 0 0 4px var(--accent-soft)}
input[type=checkbox]:checked::after{content:"";width:4px;height:8px;border:2px solid #fff;border-top:0;border-left:0;
  transform:rotate(42deg) translate(-1px,-1px)}
input[type=radio]:checked::after{content:"";width:6px;height:6px;border-radius:50%;background:#fff}
input[type=checkbox]:disabled,input[type=radio]:disabled{opacity:.4;cursor:not-allowed}
input[type=color]{padding:3px;height:2.5em;cursor:pointer}

/* ---------- кнопки ---------- */
.btn{position:relative;display:inline-flex;align-items:center;justify-content:center;gap:.45em;
  background:var(--panel2);border:1px solid var(--line);border-radius:var(--radius-s);
  padding:.55em 1.05em;font-weight:550;font-size:.92rem;cursor:pointer;white-space:nowrap;
  transition:transform .14s var(--ease),border-color .15s,background .15s,box-shadow .18s;box-shadow:var(--edge)}
.btn:hover{border-color:var(--line2);background:var(--panel3);transform:translateY(-1px)}
.btn:active{transform:translateY(0) scale(.98)}
.btn:disabled{opacity:.42;cursor:not-allowed;transform:none}
.btn.dim{opacity:.55}
.btn.primary{background:linear-gradient(180deg,var(--accent2),var(--accent));border-color:transparent;color:#fff;
  box-shadow:0 1px 0 rgba(255,255,255,.28) inset,0 8px 22px rgba(217,45,58,.32)}
.btn.primary:hover{background:linear-gradient(180deg,#ff6d76,#e2323f);box-shadow:0 1px 0 rgba(255,255,255,.3) inset,0 12px 28px rgba(217,45,58,.45)}
.btn.danger{background:rgba(255,99,105,.1);border-color:rgba(255,99,105,.32);color:var(--err)}
.btn.danger:hover{background:rgba(255,99,105,.18);border-color:rgba(255,99,105,.5)}
.btn.wide{width:100%}
.btn.tiny{padding:.3em .66em;font-size:.78rem;border-radius:9px}
.btn.icon{padding:.5em .62em;font-size:1rem;line-height:1}
.pinwrap{display:inline-flex;align-items:stretch;gap:4px}
.btn.star{padding:.42em .52em;min-width:2.15em;font-size:.9rem;opacity:.55}
.btn.star:hover{opacity:1}
.btn.star.on{opacity:1;color:#ffcf5a;border-color:rgba(255,207,90,.4);background:rgba(255,207,90,.1)}

/* ---------- карточки ---------- */
.card{position:relative;background:linear-gradient(180deg,rgba(255,255,255,.035),rgba(255,255,255,0) 42%),var(--panel);
  border:1px solid var(--line);border-radius:var(--radius);padding:1.2rem 1.3rem;
  box-shadow:var(--glow),var(--edge);transition:border-color .18s,box-shadow .22s,transform .18s var(--ease)}
.card:hover{border-color:var(--line2)}
.grid2>.card:hover,.cols>.card:hover{transform:translateY(-2px)}
.card.danger-zone{border-color:rgba(255,99,105,.3);background:linear-gradient(160deg,rgba(255,99,105,.09),var(--panel) 60%)}
.card.pinned{border-color:rgba(255,207,90,.34)}
.card.pinned::before{content:"";position:absolute;left:1.2rem;right:1.2rem;top:-1px;height:2px;border-radius:2px;
  background:linear-gradient(90deg,transparent,rgba(255,207,90,.85),transparent)}
.row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
.row.end{justify-content:flex-end}
.spacer{flex:1}
.err{color:var(--err);min-height:1.4em;margin-top:.5rem;font-size:.9rem}

/* ---------- вход ---------- */
.center{min-height:100vh;display:grid;place-items:center;padding:1.2rem}
.login{width:100%;max-width:400px;text-align:center;display:flex;flex-direction:column;gap:.8rem;
  padding:2.6rem 2rem 1.8rem;border-radius:24px;overflow:hidden;
  background:linear-gradient(180deg,rgba(255,255,255,.05),rgba(255,255,255,0) 50%),rgba(18,18,24,.72);
  backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);
  box-shadow:0 30px 80px rgba(0,0,0,.55),0 0 0 1px rgba(255,255,255,.04),var(--edge);
  animation:rise .5s var(--ease) both}
.login::before{content:"";position:absolute;inset:0 0 auto;height:2px;
  background:linear-gradient(90deg,transparent,var(--accent2),transparent);background-size:220% 100%;animation:sweep 6s linear infinite}
.login::after{content:"";position:absolute;width:320px;height:320px;left:50%;top:-190px;transform:translateX(-50%);
  background:radial-gradient(closest-side,rgba(255,90,100,.32),transparent);pointer-events:none;z-index:-1}
@keyframes sweep{from{background-position:120% 0}to{background-position:-120% 0}}
.login>*{animation:pop .5s var(--ease) both}
.login>*:nth-child(2){animation-delay:.05s}.login>*:nth-child(3){animation-delay:.1s}
.login>*:nth-child(4){animation-delay:.15s}.login>*:nth-child(5){animation-delay:.2s}.login>*:nth-child(6){animation-delay:.25s}
@keyframes pop{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.login h1{font-size:1.5rem;margin-bottom:0}
.login p{margin:0 0 .4rem}
.login input{width:100%;text-align:center;letter-spacing:.18em;font-size:1.02rem;padding:.8em .9em;border-radius:13px}
.login .btn{margin-top:.2rem;padding:.8em 1em;font-weight:600;font-size:.98rem}
.logo{width:56px;height:56px;border-radius:18px;margin:0 auto .4rem;display:grid;place-items:center;
  font-size:1.6rem;font-weight:750;color:#fff;letter-spacing:-.02em;
  background:linear-gradient(145deg,var(--accent2),var(--accent-d));
  box-shadow:0 12px 30px rgba(217,45,58,.45),0 1px 0 rgba(255,255,255,.35) inset}
.spinner{width:34px;height:34px;border:3px solid var(--line);border-top-color:var(--accent2);border-radius:50%;margin:0 auto;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* ---------- каркас: боковое меню + сцена ---------- */
.shell{display:grid;grid-template-columns:var(--side) minmax(0,1fr);min-height:100vh}
.side{position:sticky;top:0;height:100vh;display:flex;flex-direction:column;gap:.4rem;
  padding:1.15rem .85rem .9rem;border-right:1px solid var(--line);
  background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,0)),rgba(10,10,14,.72);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);z-index:30}
.brand{display:flex;gap:.75rem;align-items:center;min-width:0;padding:.2rem .55rem .7rem}
.brand .logo{width:40px;height:40px;border-radius:13px;font-size:1.15rem;margin:0;flex:none}
.brand b{display:block;line-height:1.2;font-size:.98rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.brand span{font-size:.7rem;color:var(--muted);letter-spacing:.09em;text-transform:uppercase}
.nav{flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column;gap:.1rem;padding-right:2px;scrollbar-width:none}
.nav::-webkit-scrollbar{display:none}
.navgroup{margin:.8rem .65rem .3rem;font-size:.68rem;font-weight:650;letter-spacing:.13em;text-transform:uppercase;color:var(--faint)}
.navgroup:first-child{margin-top:.2rem}
.nav button{display:flex;align-items:center;gap:.7rem;width:100%;text-align:left;
  background:none;border:0;border-radius:11px;padding:.4em .7em;cursor:pointer;color:var(--muted);
  font-size:.91rem;font-weight:520;position:relative;
  transition:background .15s,color .15s,transform .14s var(--ease)}
.nav button .ico{display:grid;place-items:center;width:29px;height:29px;border-radius:9px;flex:none;
  color:var(--muted);transition:background .15s,color .15s,box-shadow .15s}
.nav button:hover{color:var(--text);background:var(--hover)}
.nav button:hover .ico{color:var(--text)}
.nav button.active{color:var(--text);background:linear-gradient(90deg,var(--accent-soft),rgba(255,90,100,.02))}
.nav button.active .ico{color:#fff;background:linear-gradient(145deg,var(--accent2),var(--accent));
  box-shadow:0 6px 16px rgba(217,45,58,.4)}
.nav button.active::before{content:"";position:absolute;left:-.85rem;top:22%;bottom:22%;width:3px;
  border-radius:0 3px 3px 0;background:var(--accent2);box-shadow:0 0 12px var(--accent2)}
.ico svg{width:17px;height:17px;display:block;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
.sidefoot{display:flex;flex-direction:column;gap:.55rem;padding-top:.8rem;border-top:1px solid var(--line)}
.status{display:flex;align-items:center;gap:.6rem;padding:.55rem .7rem;border-radius:12px;
  background:var(--panel);border:1px solid var(--line);font-size:.82rem;color:var(--muted)}
.status b{color:var(--text);font-weight:600}
.dot{width:9px;height:9px;border-radius:50%;background:var(--ok);flex:none;
  box-shadow:0 0 0 4px rgba(61,214,140,.16);animation:pulse 2.9s ease-in-out infinite}
.dot.off{background:var(--warn);box-shadow:0 0 0 4px rgba(245,165,36,.16);animation:none}
@keyframes pulse{0%,100%{box-shadow:0 0 0 3px rgba(61,214,140,.22)}50%{box-shadow:0 0 0 8px rgba(61,214,140,.03)}}
.footbtns{display:flex;gap:.45rem}
.footbtns .btn{flex:1}
.footbtns .btn.icon{flex:none}

.stage{min-width:0;display:flex;flex-direction:column}
.mbar{display:none}
.scrim{display:none}
.main{padding:2rem clamp(1.1rem,3.6vw,3rem) 4rem;max-width:1280px;margin:0 auto;width:100%}
#content>*{animation:rise .32s var(--ease) both}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}

/* заголовок страницы */
.head{display:flex;justify-content:space-between;align-items:flex-end;gap:1rem;margin-bottom:1.6rem;flex-wrap:wrap}
.head h1{font-size:1.85rem;letter-spacing:-.03em;margin:0}
.head p{margin:.35rem 0 0;max-width:62ch;font-size:.95rem}

/* ---------- обзор ---------- */
.stats{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:.9rem;margin-bottom:1.5rem}
.stat{padding:1.05rem 1.15rem;overflow:hidden}
.stat::before{content:"";position:absolute;left:0;top:0;right:0;height:2px;
  background:linear-gradient(90deg,var(--accent2),transparent 75%);opacity:.8}
.stat::after{content:"";position:absolute;right:-30px;top:-30px;width:90px;height:90px;border-radius:50%;
  background:radial-gradient(closest-side,var(--accent-soft),transparent);pointer-events:none}
.stat:hover{transform:translateY(-2px)}
.stat small{display:block;color:var(--muted);font-size:.78rem;font-weight:550;margin-bottom:.35rem;letter-spacing:.01em}
.stat b{font-size:1.65rem;font-weight:680;font-variant-numeric:tabular-nums;letter-spacing:-.03em;line-height:1.1}
.stat.ok b{color:var(--ok)} .stat.warn b{color:var(--warn)}
.stat.ok::before{background:linear-gradient(90deg,var(--ok),transparent 75%)}
.stat.warn::before{background:linear-gradient(90deg,var(--warn),transparent 75%)}
.guilds{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:.9rem}
.guild{display:flex;gap:.9rem;align-items:center;padding:.9rem 1.05rem}
.guild:hover{transform:translateY(-2px)}
.guild img,.ava{width:46px;height:46px;border-radius:14px;background:var(--panel3);object-fit:cover;flex:none}
.ava{display:grid;place-items:center;font-weight:700;color:var(--muted);font-size:1.1rem}
.guild b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.section-title{margin:2rem 0 .85rem;color:var(--muted);font-size:.73rem;text-transform:uppercase;letter-spacing:.14em;
  font-weight:650;display:flex;align-items:center;gap:.8rem}
.section-title::after{content:"";flex:1;height:1px;background:linear-gradient(90deg,var(--line2),transparent)}

/* ---------- модули ---------- */
.list{display:flex;flex-direction:column;gap:.55rem}
.item{display:flex;align-items:center;gap:.9rem;padding:.8rem 1.1rem}
.item .grow{flex:1;min-width:0}
.item .grow b{display:block;overflow:hidden;text-overflow:ellipsis;font-weight:600}
.item.dim{opacity:.6}
.chip{display:inline-block;font-size:.7rem;font-weight:600;padding:.16em .66em;border-radius:99px;
  border:1px solid var(--line);background:var(--hover);color:var(--muted);vertical-align:middle;margin-left:.5rem;letter-spacing:.01em}
.chip.ok{color:var(--ok);border-color:rgba(61,214,140,.35);background:rgba(61,214,140,.1)}
.chip.err{color:var(--err);border-color:rgba(255,99,105,.38);background:rgba(255,99,105,.1)}
.chip.off{color:var(--muted)}
.switch{width:48px;height:28px;border-radius:99px;border:1px solid var(--line2);background:var(--panel3);
  position:relative;cursor:pointer;flex:none;padding:0;transition:background .2s,border-color .2s,box-shadow .2s}
.switch::after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;
  background:#c9c9d4;transition:transform .22s var(--ease),background .2s;box-shadow:0 2px 6px rgba(0,0,0,.45)}
.switch.on{background:linear-gradient(180deg,var(--accent2),var(--accent));border-color:transparent;box-shadow:0 0 0 4px var(--accent-soft)}
.switch.on::after{transform:translateX(20px);background:#fff}
.switch:disabled{opacity:.4;cursor:not-allowed}
.toolbar{display:flex;gap:.65rem;margin-bottom:1rem;flex-wrap:wrap;align-items:flex-end}
.toolbar input[type=search]{flex:1;min-width:200px}
.toolbar label{display:flex;flex-direction:column;gap:.3rem;font-size:.8rem;color:var(--muted)}

/* ---------- настройки ---------- */
.split{display:grid;grid-template-columns:230px 1fr;gap:1.1rem;align-items:start}
.stick{position:sticky;top:1.2rem;align-self:start;max-height:calc(100vh - 2.4rem);overflow:auto}
.files{display:flex;flex-direction:column;gap:.3rem}
.files button{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-s);padding:.58em .85em;
  text-align:left;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.9rem;
  transition:border-color .15s,background .15s}
.files button:hover{border-color:var(--line2);background:var(--panel2)}
.files button.active{border-color:var(--accent-line);background:linear-gradient(90deg,var(--accent-soft),rgba(255,90,100,.02));color:var(--text)}
.editor{width:100%;min-height:62vh;resize:vertical;font-family:ui-monospace,"JetBrains Mono",SFMono-Regular,Menlo,Consolas,monospace;
  font-size:.85rem;line-height:1.6;white-space:pre;tab-size:2;background:var(--code)}
.note{border:1px solid var(--accent-line);background:linear-gradient(90deg,var(--accent-soft),rgba(255,90,100,.02));
  padding:.7rem 1rem;color:var(--muted);font-size:.88rem;margin-bottom:1.1rem;border-radius:var(--radius-s)}

/* ---------- логи ---------- */
.logbox{background:var(--code);border:1px solid var(--line);border-radius:var(--radius);height:64vh;overflow:auto;
  padding:.9rem 1.05rem;font-size:.8rem;line-height:1.6;box-shadow:inset 0 2px 16px rgba(0,0,0,.5)}
.ln{white-space:pre-wrap;word-break:break-word;color:#c9c9d4}
.ln.e{color:#ff8a8f} .ln.w{color:#ffcb7d} .ln.o{color:#7fe3ae}

/* ---------- формы и управление ---------- */
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:1.1rem}
.form{display:flex;flex-direction:column;gap:.8rem}
.form label{display:flex;flex-direction:column;gap:.3rem;font-size:.84rem;font-weight:520;color:var(--muted)}
textarea.msg{min-height:132px;width:100%;resize:vertical;font-family:inherit;line-height:1.55}
.msg.tall{min-height:26vh;line-height:1.65}
.pager{display:flex;gap:.4rem;flex-wrap:wrap;margin-bottom:1rem}
.pager button{background:var(--panel);border:1px solid var(--line);border-radius:99px;padding:.4em 1em;cursor:pointer;
  color:var(--muted);font-size:.85rem;font-weight:550;transition:border-color .15s,color .15s,background .15s}
.pager button:hover{border-color:var(--line2);color:var(--text)}
.pager button.active{border-color:transparent;color:#fff;background:linear-gradient(180deg,var(--accent2),var(--accent));
  box-shadow:0 6px 16px rgba(217,45,58,.3)}
.rows{display:flex;flex-direction:column;gap:.45rem}
.r{display:flex;gap:.8rem;align-items:center;padding:.7rem .95rem;
  background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,0)),var(--panel);
  border:1px solid var(--line);border-radius:var(--radius-s);cursor:pointer;text-align:left;width:100%;
  transition:border-color .15s,background .15s,transform .15s var(--ease)}
.r:hover{border-color:var(--line2);transform:translateX(3px)}
.r.sel{border-color:var(--accent-line);background:linear-gradient(90deg,var(--accent-soft),rgba(255,90,100,.02));
  box-shadow:0 0 0 1px var(--accent-soft)}
.r .grow{flex:1;min-width:0}
.r b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
.r small{display:block;color:var(--muted);font-size:.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kv{display:grid;grid-template-columns:150px 1fr;gap:.5rem .9rem;font-size:.9rem}
.kv dt{color:var(--muted)} .kv dd{margin:0;word-break:break-word}
.timeline{display:flex;flex-direction:column;gap:.6rem;margin:.5rem 0 0;padding:0;list-style:none}
.timeline li{border-left:2px solid var(--line2);padding-left:.85rem;font-size:.87rem;transition:border-color .15s}
.timeline li:hover{border-left-color:var(--accent2)}
.timeline li span{color:var(--muted);font-size:.78rem}
.steps{margin:.4rem 0 0;padding-left:1.15rem;font-size:.88rem;color:var(--muted)}
.warnline{color:var(--warn);font-size:.85rem}
.embed{border-left:4px solid var(--accent);background:var(--panel2);border-radius:var(--radius-s);
  padding:.85rem 1rem;margin-bottom:.7rem;box-shadow:var(--edge)}
.embed b{display:block;margin-bottom:.3rem}
.embed .fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.4rem .9rem;margin-top:.55rem;font-size:.85rem}
.embed .fields i{font-style:normal;color:var(--muted);display:block}
.embed img{max-width:240px;border-radius:9px;margin-top:.6rem;display:block;border:1px solid var(--line)}
.btns{display:flex;gap:.45rem;flex-wrap:wrap;margin-top:.55rem}
.bar{height:9px;border-radius:99px;background:var(--panel3);overflow:hidden;margin:.4rem 0 .7rem}
.bar i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .4s var(--ease)}
.bar i.warn{background:linear-gradient(90deg,#b9770e,var(--warn))} .bar i.ok{background:linear-gradient(90deg,#1f9d63,var(--ok))}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:1.1rem}
.picks{max-height:260px;overflow:auto;border:1px solid var(--line);border-radius:var(--radius-s);padding:.35rem;
  display:flex;flex-direction:column;gap:.15rem;background:var(--field)}
.picks label{display:flex;gap:.6rem;align-items:center;text-align:left;padding:.45rem .55rem;border-radius:9px;
  border:1px solid transparent;cursor:pointer;transition:background .12s,border-color .12s;color:var(--text)}
.picks label:hover{background:var(--hover)}
.picks label:has(input:checked){background:linear-gradient(90deg,var(--accent-soft),rgba(255,90,100,.02));border-color:var(--accent-line)}
.picks .grow{flex:1;min-width:0}
.picks b{display:block;font-weight:600;font-size:.88rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.picks small{display:block;color:var(--muted);font-size:.75rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.picks .none{padding:.6rem .6rem;color:var(--muted);font-size:.85rem}
.pickline{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin-top:.5rem;font-size:.85rem;color:var(--muted)}
.pickline b{color:var(--text);font-weight:600}
.previewbox{margin:.8rem 0;display:flex;flex-direction:column;gap:.55rem}

/* плитки «Управления» */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem}
.tile{display:flex;flex-direction:column;gap:.6rem;padding:1.1rem 1.2rem;overflow:hidden}
.tile:hover{transform:translateY(-3px);border-color:var(--line2)}
.tile::after{content:"";position:absolute;left:0;right:0;top:0;height:2px;opacity:0;
  background:linear-gradient(90deg,transparent,var(--accent2),transparent);transition:opacity .2s}
.tile:hover::after{opacity:.9}
.tile .ic{width:38px;height:38px;border-radius:12px;display:grid;place-items:center;font-size:1.1rem;
  background:var(--accent-soft);border:1px solid var(--accent-line)}
.tile.warn-tile .ic{background:rgba(245,165,36,.12);border-color:rgba(245,165,36,.38)}
.tile.ok-tile .ic{background:rgba(61,214,140,.12);border-color:rgba(61,214,140,.35)}
.tile h3{margin:0;font-size:1rem}
.tile p{margin:0;color:var(--muted);font-size:.84rem;line-height:1.5;flex:1}
.tile .out{font-size:.82rem}
.tile .out:empty{display:none}
.tile .row{margin-top:.2rem}
.tiles .sticker{cursor:pointer;align-items:center;text-align:center;padding:.9rem}
.tiles .sticker img{width:100%;max-width:150px;aspect-ratio:1/1;object-fit:contain;border-radius:14px;
  background:var(--hover);padding:.4rem;transition:transform .22s var(--ease)}
.tiles .sticker:hover img{transform:scale(1.07) rotate(-1.5deg)}
.tiles .sticker.sel{border-color:var(--accent-line);box-shadow:var(--glow),0 0 0 3px var(--accent-soft)}
.tiles .sticker b{font-size:.85rem}
.diff .was{color:var(--muted);font-size:.78rem}
.diff del{text-decoration:none;color:#ff8a8f;background:rgba(255,99,105,.13);border-radius:4px;padding:0 .25em}
.diff ins{text-decoration:none;color:#7fe3ae;background:rgba(61,214,140,.13);border-radius:4px;padding:0 .25em}

/* скелетон загрузки */
.skel{position:relative;overflow:hidden;height:15px;border-radius:8px;background:var(--panel2);flex:1}
.skel::after{content:"";position:absolute;inset:0;transform:translateX(-100%);
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.08),transparent);animation:shimmer 1.2s infinite}
@keyframes shimmer{to{transform:translateX(100%)}}
.skelwrap{display:flex;flex-direction:column;gap:.85rem}
.skelrow{display:flex;gap:.7rem;align-items:center}

/* окна и уведомления */
dialog{background:var(--panel2);color:var(--text);border:1px solid var(--line2);border-radius:20px;padding:1.5rem;
  max-width:520px;width:calc(100% - 2rem);box-shadow:0 30px 80px rgba(0,0,0,.6)}
dialog[open]{animation:pop .25s var(--ease) both}
dialog::backdrop{background:rgba(3,3,6,.7);backdrop-filter:blur(5px)}
#toasts{position:fixed;right:1.1rem;bottom:1.1rem;display:flex;flex-direction:column;gap:.55rem;z-index:60}
.toast{background:var(--panel3);border:1px solid var(--line2);border-left:4px solid var(--accent2);border-radius:12px;
  padding:.75rem 1rem;max-width:360px;font-size:.92rem;box-shadow:0 14px 40px rgba(0,0,0,.5);animation:rise .25s var(--ease) both}
.toast.ok{border-left-color:var(--ok)} .toast.err{border-left-color:var(--err)}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:#2c2c36;border-radius:99px;border:2px solid transparent;background-clip:padding-box}
::-webkit-scrollbar-thumb:hover{background:#43434f;background-clip:padding-box}

/* ---------- светлая тема ---------- */
html[data-theme=light]{
  --bg:#f3f3f8; --bg2:#f8f8fc; --panel:#ffffff; --panel2:#f6f6fb; --panel3:#ececf4;
  --line:rgba(20,20,40,.09); --line2:rgba(20,20,40,.17);
  --text:#14141c; --muted:#666677; --faint:#9a9aa8;
  --accent:#d42a37; --accent2:#ef4553; --accent-d:#a8121f;
  --accent-soft:rgba(217,45,58,.09); --accent-line:rgba(217,45,58,.35);
  --ok:#17935a; --warn:#b36f0a; --err:#d1303b;
  --field:#ffffff; --hover:rgba(20,20,40,.05); --code:#10111a;
  --glow:0 1px 2px rgba(20,20,50,.06),0 14px 34px rgba(30,30,70,.08); --edge:inset 0 1px 0 rgba(255,255,255,.9);
  color-scheme:light
}
html[data-theme=light] body::before{background:
  radial-gradient(52% 44% at 8% -6%, rgba(217,45,58,.10), transparent 70%),
  radial-gradient(44% 40% at 100% 108%, rgba(120,70,255,.07), transparent 70%),
  linear-gradient(180deg, var(--bg2), var(--bg))}
html[data-theme=light] body::after{background-image:radial-gradient(rgba(20,20,50,.08) 1px, transparent 1.2px);opacity:.5}
html[data-theme=light] .card{background:var(--panel)}
html[data-theme=light] .side{background:rgba(255,255,255,.72)}
html[data-theme=light] .login{background:rgba(255,255,255,.82);box-shadow:0 30px 70px rgba(30,30,70,.14),0 0 0 1px rgba(20,20,50,.05)}
html[data-theme=light] .btn.primary{color:#fff}
html[data-theme=light] .switch::after{background:#fff}
html[data-theme=light] .ln{color:#d7d9e6}
html[data-theme=light] .btn.star.on{color:#a97a12;border-color:#d8c48a;background:#fff8e6}
html[data-theme=light] .skel::after{background:linear-gradient(90deg,transparent,rgba(20,20,50,.07),transparent)}
html[data-theme=light] ::-webkit-scrollbar-thumb{background:#c8c8d6}
html[data-theme=light] dialog{background:#fff}
html[data-theme=light] .toast{background:#fff}
html[data-theme=light] .login::after{background:radial-gradient(closest-side,rgba(239,69,83,.22),transparent)}

@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.001s!important;animation-delay:0s!important;transition-duration:.001s!important}
  .btn:hover,.r:hover,.stat:hover,.guild:hover,.tile:hover,.grid2>.card:hover,.cols>.card:hover,.tiles .sticker:hover img{transform:none}
  .skel::after{animation:none;opacity:.4}
}

/* ---------- планшет и телефон ---------- */
@media (max-width:1000px){
  .shell{grid-template-columns:minmax(0,1fr)}
  .side{position:fixed;left:0;top:0;bottom:0;width:min(84vw,300px);transform:translateX(-102%);
    transition:transform .3s var(--ease);background:var(--panel);box-shadow:0 0 80px rgba(0,0,0,.6)}
  html[data-theme=light] .side{background:#fff}
  .shell.open .side{transform:none}
  .scrim{display:block;position:fixed;inset:0;z-index:25;background:rgba(3,3,6,.6);backdrop-filter:blur(3px);
    opacity:0;pointer-events:none;transition:opacity .25s}
  .shell.open .scrim{opacity:1;pointer-events:auto}
  .mbar{display:flex;align-items:center;gap:.75rem;position:sticky;top:0;z-index:20;padding:.7rem 1rem;
    border-bottom:1px solid var(--line);background:rgba(10,10,14,.8);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px)}
  html[data-theme=light] .mbar{background:rgba(255,255,255,.85)}
  .mbar .logo{width:32px;height:32px;border-radius:10px;font-size:.95rem;margin:0}
  .mbar b{font-size:.98rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .main{padding:1.4rem 1rem 3rem}
  .head h1{font-size:1.55rem}
}
@media (max-width:820px){
  .split{grid-template-columns:1fr!important}
  .stick{position:static;max-height:none}
  .files{flex-direction:row;overflow-x:auto}
  .files button{flex:none}
  .editor{min-height:50vh}
  .kv{grid-template-columns:110px 1fr}
  .stats{grid-template-columns:repeat(2,minmax(0,1fr))}
  .grid2,.cols{grid-template-columns:1fr}
  .item{flex-wrap:wrap}
}

/* ============================================================
   Строгий и быстрый слой: графит и приглушённый красный,
   плоские поверхности, без размытия и бесконечных анимаций.
   Лежит в конце, поэтому перекрывает всё выше.
   ============================================================ */
:root{
  --bg:#08090c; --bg2:#0d0e12;
  --panel:#12141a; --panel2:#171a21; --panel3:#1f232c;
  --line:rgba(255,255,255,.07); --line2:rgba(255,255,255,.13);
  --text:#e8eaef; --muted:#8a8f9b; --faint:#5c616d;
  --accent:#c4202e; --accent2:#e5434f; --accent-d:#97141f;
  --accent-soft:rgba(229,67,79,.11); --accent-line:rgba(229,67,79,.4);
  --ok:#3fcf8e; --warn:#e8a33a; --err:#f0646d;
  --field:#0d0f14; --code:#07080b;
  --radius:12px; --radius-s:8px;
  --glow:0 1px 2px rgba(0,0,0,.45); --edge:none;
}
body::before{background:
  radial-gradient(900px 540px at 10% -10%, rgba(196,32,46,.20), transparent 70%),
  radial-gradient(820px 620px at 108% 112%, rgba(58,92,168,.16), transparent 70%),
  linear-gradient(180deg,var(--bg2),var(--bg))}
body::after{opacity:1;
  background-image:
    linear-gradient(rgba(255,255,255,.04) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.04) 1px,transparent 1px);
  background-size:56px 56px;
  -webkit-mask-image:radial-gradient(ellipse 90% 70% at 28% 0%,#000,transparent 78%);
  mask-image:radial-gradient(ellipse 90% 70% at 28% 0%,#000,transparent 78%)}
h1,h2,h3{font-weight:600}
.head h1{font-size:1.7rem;font-weight:650}

/* скорость: ни размытия, ни вечных анимаций, короткие переходы */
.side,.login,.mbar,.scrim,dialog::backdrop{backdrop-filter:none;-webkit-backdrop-filter:none}
*,*::before,*::after{transition-duration:.1s!important}
.login,.login>*,.login::before,dialog[open],.toast,.dot,.skel::after{animation:none!important}
#content>*{animation:fadein .12s ease-out both}
@keyframes fadein{from{opacity:0}to{opacity:1}}
.btn:hover,.r:hover,.card:hover,.tile:hover,.stat:hover,.guild:hover,
.grid2>.card:hover,.cols>.card:hover,.tiles .sticker:hover img{transform:none}
.skel::after{display:none}

/* строгие плоские поверхности */
.card,.r,.stat,.tile,.guild{background:var(--panel);box-shadow:var(--glow)}
.card:hover,.r:hover{border-color:var(--line2)}
.side{background:rgba(10,11,15,.92)}
.login{background:var(--panel);box-shadow:0 24px 60px rgba(0,0,0,.55);border-radius:18px}
.login::after{display:none}
.stat::after{display:none}
.stat::before{height:2px;opacity:1;background:var(--accent)}
.stat.ok::before{background:var(--ok)} .stat.warn::before{background:var(--warn)}
.stat b{font-weight:650}
.logo{background:var(--accent);box-shadow:none;border-radius:12px}
.brand .logo{border-radius:10px}
.btn{box-shadow:none;background:var(--panel2);font-weight:550}
.btn:hover{background:var(--panel3)}
.btn.primary{background:var(--accent);box-shadow:none}
.btn.primary:hover{background:var(--accent2);box-shadow:none}
.nav button.active{background:var(--accent-soft)}
.nav button.active .ico{background:var(--accent);box-shadow:none}
.nav button.active::before{box-shadow:none}
.pager button.active{background:var(--accent);box-shadow:none}
.switch.on{background:var(--accent);box-shadow:none}
input[type=checkbox]:checked,input[type=radio]:checked{background:var(--accent);box-shadow:none}
.tile .ic{border-radius:9px}
.bar i{background:var(--accent)}
.bar i.warn{background:var(--warn)} .bar i.ok{background:var(--ok)}
.card.pinned::before{display:none}
.card.danger-zone{background:var(--panel)}
.section-title::after{background:var(--line)}

html[data-theme=light]{
  --bg:#eef0f4; --bg2:#f6f7fa; --panel:#ffffff; --panel2:#f4f5f8; --panel3:#e9ebf0;
  --line:rgba(15,20,40,.10); --line2:rgba(15,20,40,.18);
  --text:#14171f; --muted:#626877; --faint:#9298a6;
  --accent:#b8202c; --accent2:#d63a46; --field:#fff; --code:#10121a;
  --glow:0 1px 2px rgba(20,25,50,.07);
}
html[data-theme=light] body::before{background:
  radial-gradient(900px 540px at 10% -10%, rgba(196,32,46,.10), transparent 70%),
  radial-gradient(820px 620px at 108% 112%, rgba(58,92,168,.09), transparent 70%),
  linear-gradient(180deg,var(--bg2),var(--bg))}
html[data-theme=light] body::after{
  background-image:
    linear-gradient(rgba(15,20,40,.05) 1px,transparent 1px),
    linear-gradient(90deg,rgba(15,20,40,.05) 1px,transparent 1px);
  background-size:56px 56px}
html[data-theme=light] .side{background:rgba(255,255,255,.94)}
html[data-theme=light] .login{background:#fff}
@media (max-width:1000px){
  .side{background:var(--panel)}
  .mbar{background:rgba(10,11,15,.96)}
  html[data-theme=light] .mbar{background:rgba(255,255,255,.96)}
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
      else if (k === 'style') el.style.cssText = String(v); /* setAttribute('style') режет CSP, cssText нет */
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
    h('p', { class: 'muted' }, 'Введите пароль, чтобы войти'),
    input,
    h('button', { class: 'btn primary wide', type: 'submit' }, 'Войти'),
    err
  );
  $app.replaceChildren(h('div', { class: 'center' }, form));
  input.focus();
}

/* ---------- тема и избранное ---------- */
/* Тема и закреплённые экраны живут в localStorage браузера: сервер их не хранит,
   поэтому каждый заходит со своими настройками и ничего не ломает другому. */
function applyTheme() {
  let t = '';
  try { t = localStorage.getItem('hz_theme') || ''; } catch (e) { /* приватный режим */ }
  document.documentElement.dataset.theme = t === 'light' ? 'light' : 'dark';
}
function themeIcon() { return document.documentElement.dataset.theme === 'light' ? '☀️' : '🌙'; }
function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  try { localStorage.setItem('hz_theme', next); } catch (e) { /* приватный режим */ }
  applyTheme();
  document.querySelectorAll('.footbtns .btn.icon').forEach((b) => { b.replaceChildren(themeIcon()); });
}

const FAV_KEY = 'hz_fav';
function favGet() {
  try { return JSON.parse(localStorage.getItem(FAV_KEY)) || []; } catch (e) { return []; }
}
function favToggle(ref) {
  const list = favGet();
  const i = list.indexOf(ref);
  if (i >= 0) list.splice(i, 1); else list.push(ref);
  try { localStorage.setItem(FAV_KEY, JSON.stringify(list.slice(0, 24))); } catch (e) { /* приватный режим: не запоминаем */ }
  return list.indexOf(ref) >= 0;
}

/* ---------- каркас ---------- */
/* группа в третьей колонке — пункты меню собираются под заголовок группы */
const PAGES = [
  ['overview', 'Обзор', 'dash', 'Семья'],
  ['apps', 'Заявки', 'inbox', 'Семья'],
  ['send', 'Сообщения', 'send', 'Семья'],
  ['screens', 'Экраны', 'screen', 'Семья'],
  ['texts', 'Тексты', 'type', 'Семья'],
  ['stickers', 'Стикеры', 'smile', 'Семья'],
  ['diag', 'Диагностика', 'pulse', 'Служба'],
  ['audit', 'Ревизия', 'search', 'Служба'],
  ['shots', 'Снимки', 'camera', 'Служба'],
  ['modules', 'Модули', 'cube', 'Служба'],
  ['settings', 'Настройки', 'sliders', 'Служба'],
  ['logs', 'Логи', 'term', 'Служба'],
  ['control', 'Управление', 'power', 'Служба'],
];

/* значки — свои константы, поэтому вставка через innerHTML безопасна */
const ICONS = {
  dash: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  inbox: '<polyline points="22 12 16 12 14 15 10 15 8 12 2 12"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  screen: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
  type: '<polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/>',
  smile: '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/>',
  pulse: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/>',
  cube: '<path d="M12 2 3 7v10l9 5 9-5V7z"/><path d="m3 7 9 5 9-5M12 12v10"/>',
  sliders: '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
  term: '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
  power: '<path d="M18.36 6.64a9 9 0 1 1-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/>',
  out: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
  menu: '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
};
function icon(name) {
  const el = h('span', { class: 'ico', 'aria-hidden': 'true' });
  el.innerHTML = '<svg viewBox="0 0 24 24" focusable="false">' + (ICONS[name] || '') + '</svg>';
  return el;
}

function setMenu(open) {
  const shell = document.getElementById('shell');
  if (shell) shell.classList.toggle('open', !!open);
}

function renderShell() {
  const nav = h('nav', { class: 'nav', 'aria-label': 'Разделы панели' });
  let group = null;
  for (const [id, label, ico, g] of PAGES) {
    if (g !== group) { group = g; nav.append(h('div', { class: 'navgroup' }, g)); }
    nav.append(h('button', { 'data-page': id, title: label, onclick: () => go(id) }, icon(ico), label));
  }
  const side = h('aside', { class: 'side', id: 'side' },
    h('div', { class: 'brand' },
      h('div', { class: 'logo' }, (state.botName || 'H').slice(0, 1).toUpperCase()),
      h('div', null, h('b', null, state.botName), h('span', null, 'Панель управления'))),
    nav,
    h('div', { class: 'sidefoot' },
      h('div', { class: 'status' }, h('span', { class: 'dot', id: 'live', title: 'Бот на связи' }),
        h('span', { id: 'livetxt' }, 'Бот ', h('b', null, 'на связи'))),
      h('div', { class: 'footbtns' },
        h('button', { class: 'btn icon', title: 'Светлая или тёмная тема', onclick: toggleTheme }, themeIcon()),
        h('button', { class: 'btn', onclick: logout }, icon('out'), 'Выйти'))));
  const mbar = h('header', { class: 'mbar' },
    h('button', { class: 'btn icon', 'aria-label': 'Открыть меню', onclick: () => setMenu(true) }, icon('menu')),
    h('div', { class: 'logo' }, (state.botName || 'H').slice(0, 1).toUpperCase()),
    h('b', null, state.botName),
    h('div', { class: 'spacer' }));
  const shell = h('div', { class: 'shell', id: 'shell' },
    side,
    h('div', { class: 'scrim', onclick: () => setMenu(false) }),
    h('div', { class: 'stage' }, mbar, h('main', { class: 'main', id: 'content' })));
  $app.replaceChildren(shell);
}

function markLive(ok) {
  const dot = document.getElementById('live');
  if (!dot) return;
  dot.classList.toggle('off', !ok);
  dot.title = ok ? 'Панель отвечает, бот на связи' : 'Панель не смогла получить данные';
  const t = document.getElementById('livetxt');
  if (t) t.replaceChildren('Бот ', h('b', null, ok ? 'на связи' : 'не отвечает'));
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
  setMenu(false);
  window.scrollTo({ top: 0 });
  const content = document.getElementById('content');
  content.replaceChildren(skeleton());
  try {
    const view = await ({ overview: pageOverview, apps: pageApps, send: pageSend, screens: pageScreens,
      texts: pageTexts, stickers: pageStickers,
      diag: pageDiag, audit: pageAudit, shots: pageShots,
      modules: pageModules, settings: pageSettings, logs: pageLogs, control: pageControl })[page]();
    if (state.page === page) content.replaceChildren(view);
    markLive(true);
  } catch (e) {
    markLive(false);
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
      h('div', { class: 'grow' }, h('b', null, m.name.replace(/^cogs\./, ''), chip(m), m.protected ? h('span', { class: 'chip' }, 'системный') : null),
        h('span', { class: 'muted small' }, (m.about ? m.about + ' · ' : '') + m.file)),
      h('button', { class: 'btn', disabled: m.protected || (!m.loaded && enabled === false), onclick: () => act(m, 'reload') }, m.loaded ? 'Перезагрузить' : 'Запустить'),
      h('button', { class: 'switch' + (enabled ? ' on' : ''), role: 'switch', 'aria-checked': String(enabled), 'aria-label': 'Включить или отключить ' + m.name, disabled: m.protected, onclick: () => act(m, enabled ? 'disable' : 'enable') })
    );
  }

  function render() {
    const q = search.value.trim().toLowerCase();
    const match = (m) => !q || m.name.toLowerCase().includes(q) || (m.file || '').toLowerCase().includes(q);
    list.replaceChildren(...data.modules.filter((m) => !m.internal && match(m)).map(row));
    internal.replaceChildren(...data.modules.filter((m) => m.internal && match(m)).map((m) =>
      h('div', { class: 'card item dim' }, h('div', { class: 'grow' }, h('b', null, m.file),
        h('span', { class: 'muted small' }, (m.about ? m.about + ' · ' : '')
          + 'main.py не грузит файлы с «_» — так коги выключают, чтобы экономить память. '
          + 'Включить можно только переименованием файла в репозитории')))));
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
    data.modules.some((m) => m.internal) ? [h('div', { class: 'section-title' }, 'Не загружаются (имя с «_»)'), internal] : null
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

/* ---------- Тексты бота ---------- */
/* Ключи берутся из самого кода (utils/web_tools.py их сканирует), поэтому список не
   отстаёт: новый get_text в коге появляется здесь сам. Правим три хранилища:
   config.json["texts"], hall_settings.json и golos_guild.json. */
const TEXT_PLACEHOLDERS = ['{nick}', '{name}', '{mention}', '{server}', '{bot_name}', '{title}',
  '{time}', '{info}', '{static}', '{recruit_channel}', '{reason}', '{rank}', '{days}', '{hours}'];

async function pageTexts() {
  const d = await api('/api/texts').catch((e) => ({ error: e.message }));
  if (d.error) return h('div', null, pageHead('Тексты', null), h('p', { class: 'err' }, d.error));

  const items = d.items;
  const search = h('input', { type: 'search', placeholder: 'Название, ключ или слово из текста…' });
  const list = h('div', { class: 'rows' });
  const edBox = h('div', { class: 'card form' }, spinner('Выбери надпись слева.'));
  let current = null;

  const titleOf = (it) => it.label || it.key.replace(/_/g, ' ');

  function renderList() {
    const needle = search.value.trim().toLowerCase();
    const cards = [];
    d.groups.forEach((g) => {
      const rows = g.items.filter((it) => !needle
        || (it.key + ' ' + titleOf(it) + ' ' + (it.value || '') + ' ' + g.title).toLowerCase().includes(needle));
      if (!rows.length) return;
      cards.push(h('div', { class: 'section-title' }, g.title + ' · ' + rows.length),
        ...rows.map((it) => h('button', {
          class: 'r' + (current && current.id === it.id ? ' sel' : ''),
          onclick: () => open(it)
        },
          h('div', { class: 'grow' }, h('b', null, titleOf(it)), h('small', { class: 'mono' }, it.key)),
          it.changed ? chip('свой текст', 'ok') : chip('как в коде', 'off'))));
    });
    list.replaceChildren(...(cards.length ? cards : [h('p', { class: 'muted' }, 'Ничего не нашлось.')]));
  }

  const KIND_LABEL = { title: 'заголовок эмбеда', text: 'текст эмбеда', button: 'кнопка', field: 'поле анкеты' };

  function previewNode(p) {
    const box = h('div', { class: 'previewbox' });
    if (p.overflow) box.append(h('p', { class: 'err small' }, 'Не влезает: ' + p.len + ' из ' + p.limit + ' знаков'));
    if (p.unknown && p.unknown.length) box.append(h('p', { class: 'warnline small' }, 'Такой подстановки нет: ' + p.unknown.join(', ')));
    if (p.kind === 'button') {
      box.append(h('div', { class: 'section-title' }, 'Как будет выглядеть кнопка'),
        h('div', { class: 'btns' }, h('span', { class: 'btn' }, p.text || '(пусто)')));
      return box;
    }
    if (p.kind === 'field') {
      box.append(h('div', { class: 'section-title' }, 'Как будет выглядеть поле'),
        h('div', { class: 'form' }, h('label', null, p.text || '(пусто)',
          h('input', { type: 'text', disabled: true, placeholder: 'так видит кандидат' }))));
      return box;
    }
    box.append(h('div', { class: 'section-title' }, 'Предпросмотр'),
      embedNode({ title: p.title || '', text: p.text, color: '#990000' }));
    return box;
  }

  async function showPreview(previewBox, area, counter, it) {
    try {
      const p = await api('/api/texts/preview', { method: 'POST', body: { id: it.id, value: area.value } });
      counter.textContent = p.len + ' / ' + p.limit + ' знаков';
      counter.className = 'small ' + (p.overflow ? 'err' : 'muted');
      previewBox.replaceChildren(previewNode(p));
    } catch (e) { /* превью не главное — ошибку держим молча */ }
  }

  function open(it) {
    current = it;
    renderList();
    const area = h('textarea', { class: 'msg tall', spellcheck: 'false' });
    area.value = it.value || '';
    const counter = h('small', { class: 'muted' });
    const previewBox = h('div');
    const dirty = () => !!current && area.value !== (current.value || '');
    state.isDirty = dirty;
    area.addEventListener('input', () => {
      state.isDirty = dirty;
      clearTimeout(area._t);
      area._t = setTimeout(() => showPreview(previewBox, area, counter, it), 380);
    });

    const tips = h('div', { class: 'btns' }, TEXT_PLACEHOLDERS.map((ph) => h('button', {
      class: 'btn tiny', title: 'Вставить в текст', onclick: () => {
        const from = area.selectionStart || area.value.length;
        const to = area.selectionEnd || from;
        area.value = area.value.slice(0, from) + ph + area.value.slice(to);
        area.focus();
        showPreview(previewBox, area, counter, it);
      }
    }, ph)));

    const saveBtn = h('button', {
      class: 'btn primary', onclick: async () => {
        saveBtn.disabled = true;
        try {
          const r = await api('/api/texts/save', { method: 'POST', body: { id: it.id, value: area.value } });
          it.value = area.value;
          it.changed = area.value !== (it.default || '');
          state.isDirty = () => false;
          renderList();
          toast('Сохранено: ' + (r.saved || it.key), 'ok');
        } catch (e) { toast(e.message, 'err'); }
        saveBtn.disabled = false;
      }
    }, 'Сохранить');

    const resetBtn = h('button', {
      class: 'btn', onclick: async () => {
        if (!(await confirmBox('Вернуть текст из кода?', 'Твоё редактирование этой надписи сотрётся.', 'Вернуть', true))) return;
        try {
          const r = await api('/api/texts/reset', { method: 'POST', body: { id: it.id } });
          it.value = r.value || ''; it.changed = false; area.value = it.value;
          state.isDirty = () => false;
          renderList(); showPreview(previewBox, area, counter, it);
          toast('Как в коде', 'ok');
        } catch (e) { toast(e.message, 'err'); }
      }
    }, 'Вернуть как в коде');

    edBox.replaceChildren(
      h('h2', null, titleOf(it)),
      h('div', { class: 'row wrap' }, chip(KIND_LABEL[it.kind] || it.kind), chip('до ' + it.limit + ' знаков', 'off'),
        chip(it.src, 'off'), it.changed ? chip('изменено', 'ok') : chip('как в коде', 'off'), counter),
      h('label', null, 'Текст', area),
      previewBox,
      h('div', { class: 'section-title' }, 'Что подставляется само'), tips,
      h('div', { class: 'row' }, saveBtn, resetBtn));
    showPreview(previewBox, area, counter, it);
  }

  search.addEventListener('input', renderList);
  renderList();
  const first = items.find((it) => it.changed) || items[0];
  if (first) open(first);

  return h('div', null,
    pageHead('Тексты бота', 'Все надписи, которые бот показывает людям: заголовки панелей, тексты заявок, кнопки, ЛС после принятия и отказа, приветствия. Правь — сразу видно, как это будет выглядеть.',
      h('button', { class: 'btn', onclick: () => go('texts') }, 'Обновить')),
    h('div', { class: 'toolbar' }, h('label', null, 'Найти', search),
      chip('всего ' + d.total, 'off'), chip('со своим текстом: ' + d.changed, d.changed ? 'ok' : 'off')),
    h('div', { class: 'split', style: 'grid-template-columns:minmax(280px,38%) 1fr' },
      h('div', { class: 'stick' }, list), edBox));
}

/* ---------- Стикеры ---------- */
async function pageStickers() {
  const d = await api('/api/stickers').catch((e) => ({ error: e.message }));
  const chans = await api('/api/channels').catch(() => ({ rows: [] }));
  if (d.error) return h('div', null, pageHead('Стикеры', null), h('p', { class: 'err' }, d.error));

  const grid = h('div', { class: 'tiles' });
  const cq = h('input', { type: 'search', placeholder: 'Имя канала…' });
  const chanSel = h('select', { size: '6' });
  const title = h('input', { type: 'text', maxlength: '256', placeholder: 'Заголовок (пусто — просто картинка)' });
  const color = h('input', { type: 'text', maxlength: '9', placeholder: 'Цвет, например 990000', value: '990000' });
  const text = h('textarea', { class: 'msg', placeholder: 'Подпись под стикером (необязательно)' });
  const out = resultBox();
  let picked = null;

  function fillChannels() {
    const needle = cq.value.trim().toLowerCase();
    const rows = chans.rows.filter((c) => c.can_write !== false
      && (!needle || (c.name + ' ' + (c.category || '')).toLowerCase().includes(needle)));
    chanSel.replaceChildren(...(rows.length ? rows.map((c) => h('option', { value: c.id },
      chanLabel(c) + (c.category ? ' · ' + c.category : ''))) : [h('option', { value: '' }, 'канал не найден')]));
    if (rows.length) chanSel.value = rows[0].id;
  }

  function pick(s) {
    picked = s;
    document.querySelectorAll('[data-sticker]').forEach((b) => b.classList.toggle('sel', b.dataset.sticker === s.name));
  }

  grid.replaceChildren(...d.rows.map((s) => h('button', {
    class: 'card tile sticker', 'data-sticker': s.name, onclick: () => pick(s), title: s.name
  },
    h('img', { src: s.url, alt: s.title, loading: 'lazy' }),
    h('b', null, s.title), h('small', { class: 'muted' }, s.size + ' КБ'))));
  if (!d.rows.length) grid.append(h('p', { class: 'muted' }, 'В папке stickers/ пусто.'));

  const sendBtn = h('button', {
    class: 'btn primary', onclick: async () => {
      if (!picked) { toast('Сначала выбери стикер — нажми на картинку.', 'err'); return; }
      if (!chanSel.value) { toast('Выбери канал.', 'err'); return; }
      sendBtn.disabled = true; out.busy();
      try {
        const r = await api('/api/stickers/send', { method: 'POST', body: {
          channel: chanSel.value, name: picked.name, title: title.value.trim(),
          text: text.value, color: color.value.trim() } });
        out.show(r);
        toast('Стикер ушёл в ' + r.where, 'ok');
      } catch (e) { out.err(e); }
      sendBtn.disabled = false;
    }
  }, 'Отправить стикер');

  fillChannels();
  cq.addEventListener('input', fillChannels);
  return h('div', null,
    pageHead('Стикеры', 'Картинки бота из папки stickers/. Выбери стикер и канал — он выйдет подписанным эмбедом, как обычный пост семьи.',
      h('button', { class: 'btn', onclick: () => go('stickers') }, 'Обновить')),
    h('div', { class: 'split' },
      h('div', null, h('div', { class: 'section-title' }, 'Выбери стикер'), grid),
      h('div', { class: 'card form' },
        h('div', { class: 'section-title' }, 'Куда и с чем'),
        h('label', null, 'Канал', cq, chanSel),
        h('label', null, 'Заголовок', title),
        h('label', null, 'Подпись', text),
        h('label', null, 'Цвет', color),
        sendBtn,
        h('div', { class: 'section-title' }, 'Результат'), out)));
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

/* карточка-плитка для одного дела: иконка, пояснение, кнопки и куда показать результат */
function tile(icon, title, help, kind, ...buttons) {
  const out = h('div', { class: 'out' });
  out.busy = (t) => out.replaceChildren(h('span', { class: 'muted small' }, t || 'Делаю…'));
  out.say = (...nodes) => out.replaceChildren(...nodes.filter(Boolean));
  out.fail = (e) => out.replaceChildren(h('span', { class: 'err' }, String(e && e.message || e)));
  const box = h('div', { class: 'tile' + (kind ? ' ' + kind : '') },
    h('div', { class: 'ic' }, icon), h('h3', null, title), h('p', null, help),
    h('div', { class: 'row' }, ...buttons), out);
  box.out = out;
  return box;
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

  /* пять плиток: дела, которых из сайта раньше не было */
  const tReload = tile('🐣', 'Применить новый код',
    'Перезагружает все рабочие модули: код, который ты подтянул с GitHub, вступает сразу — без рестарта процесса и без разрыва связи с Discord.', '',
    h('button', { class: 'btn primary', onclick: async () => {
      if (!(await confirmBox('Применить код?', 'Модули перезагрузятся по одному. Кнопки и экраны продолжат работать, но открытые ветки заявок перезапуск переживут, а их собственные кнопки — нет.', 'Применить', false))) return;
      tReload.out.busy('Перезагружаю модули…');
      try {
        const d = await api('/api/bot/reload', { method: 'POST', body: {} });
        tReload.out.say(
          h('span', { class: 'small ' + (d.failed.length ? 'warnline' : 'muted') },
            'Перезагружено ' + d.reloaded.length + (d.failed.length ? ' · встало колом: ' + d.failed.map((f) => f.name).join(', ') : '')),
          d.failed.length ? h('span', { class: 'muted small' }, ' — включи их заново на «Модули» или перезапусти бота') : null);
      } catch (e) { tReload.out.fail(e); }
    } }, 'Применить'));

  const tHide = tile('🙈', 'Спрятать дубли заново',
    'Discord иногда показывает команды, которые «Панель семьи» уже умеет открывать. Кнопка перезаписывает список сервера и вычищает огрыстки из глобального меню.', '',
    h('button', { class: 'btn', onclick: async () => {
      tHide.out.busy('Перезаписываю список команд…');
      try {
        const d = await api('/api/commands/visibility', { method: 'POST', body: { hide: true } });
        tHide.out.say(h('span', { class: 'muted small' }, d.message));
      } catch (e) { tHide.out.fail(e); }
    } }, 'Спрятать'));

  const tShow = tile('👁', 'Вернуть все команды',
    'Откатывает скрытие: каждая панель снова со своей слэш-командой. То же самое делает /reverse, но без Discord.', '',
    h('button', { class: 'btn', onclick: async () => {
      if (!(await confirmBox('Вернуть команды?', 'Спрятанные дубли снова полезут в меню Discord — это ровно то, что делает /reverse.', 'Вернуть', false))) return;
      tShow.out.busy('Возвращаю…');
      try {
        const d = await api('/api/commands/visibility', { method: 'POST', body: { hide: false } });
        tShow.out.say(h('span', { class: 'muted small' }, d.message));
      } catch (e) { tShow.out.fail(e); }
    } }, 'Вернуть'));

  const tSleep = tile('💤', 'Сон по таймеру',
    'Ночью бот только расходует трафик хостинга. Поставь таймер — он выключится сам; поднимать обратно придётся кнопкой на хостинге.', 'warn-tile',
    h('button', { class: 'btn', onclick: () => setSleep(15) }, '15 мин'),
    h('button', { class: 'btn', onclick: () => setSleep(60) }, '1 час'),
    h('button', { class: 'btn', onclick: () => setSleep(0) }, 'Отменить'));

  const tPerms = tile('🛡', 'Что бот не может',
    'Смотрит права на сервере, каналы, куда ему нельзя писать, экраны с выключенным когом и то, сколько участников он вообще видит.', 'ok-tile',
    h('button', { class: 'btn', onclick: () => check() }, 'Проверить'));

  const saySleep = (until) => {
    if (!until) { tSleep.out.say(h('span', { class: 'muted small' }, 'Сон не поставлен — бот работает, сколько нужно.')); return; }
    const left = Math.max(0, Math.round(until - Date.now() / 1000));
    tSleep.out.say(h('span', { class: 'warnline small' },
      '💤 Выключится через ' + fmtUptime(left) + ' — в ' + new Date(until * 1000).toLocaleTimeString() + '.'));
  };
  const setSleep = async (minutes) => {
    if (minutes && !(await confirmBox('Сон ' + minutes + ' мин?', 'Через ' + minutes + ' минут бот и панель выключатся сами. Включить обратно — только на хостинге.', 'Поставить', false))) return;
    tSleep.out.busy(minutes ? 'Ставлю таймер…' : 'Снимаю таймер…');
    try {
      const r = await api('/api/bot/sleep', { method: 'POST', body: { minutes } });
      saySleep(r.until);
      toast(r.message, 'ok');
    } catch (e) { tSleep.out.fail(e); }
  };
  const check = async () => {
    tPerms.out.busy('Смотрю сервер…');
    try {
      const c = await api('/api/control/center');
      if (c.error) { tPerms.out.say(h('span', { class: 'err' }, c.error)); return; }
      tPerms.out.say(
        h('span', null, c.administrator ? chip('админ сервера', 'ok') : chip('не админ', 'off')),
        c.missing.length ? h('span', { class: 'warnline small' }, 'не умеет: ' + c.missing.join(', '))
          : h('span', { class: 'muted small' }, 'права сервера — все нужные'),
        c.silent_total ? h('span', { class: 'warnline small' }, 'не может писать в ' + c.silent_total + ' каналов: '
          + c.silent.slice(0, 6).map((x) => x.name).join(', ') + (c.silent_total > 6 ? '…' : ''))
          : h('span', { class: 'muted small' }, 'пишет во все каналы'),
        c.dark.length ? h('span', { class: 'warnline small' }, c.dark.length + ' экранов с выключенным когом: '
          + c.dark.slice(0, 5).join(', ') + (c.dark.length > 5 ? '…' : '')) : null,
        c.members && c.members.note ? h('span', { class: 'warnline small' }, c.members.note)
          : h('span', { class: 'muted small' }, 'видит весь состав: ' + c.members.seen + ' человек'));
    } catch (e) { tPerms.out.fail(e); }
  };
  api('/api/bot/sleep').then((s) => saySleep(s.until)).catch(() => saySleep(null));

  return h('div', null,
    pageHead('Управление', 'Статус в Discord, питание бота и дела, которые раньше требовали консоли.',
      h('button', { class: 'btn', onclick: () => go('control') }, 'Обновить')),
    h('div', { class: 'tiles' }, tReload, tHide, tShow, tSleep, tPerms),
    h('div', { class: 'section-title' }, 'Статус и питание'),
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
        h('p', { class: 'muted small' }, 'Перезапуск помогает, если бот завис или ты изменил настройки, которые читаются только при старте. Применить код модулей можно кнопкой выше — без рестарта.'),
        h('div', { class: 'row' }, restart, stop)
      )
    )
  );
}

/* ---------- общие кусочки новых разделов ---------- */
function spinner(text) { return h('p', { class: 'muted' }, text || 'Загрузка…'); }

function chip(text, kind) { return h('span', { class: 'chip ' + (kind || '') }, text); }

function chanLabel(c) {
  if (c.kind === 'ветка') return '🧵 ' + c.name;
  // канал уже может называться эмодзи или «#…» — второй префикс превращает имя в кашу
  const first = c.name.codePointAt(0);
  const plain = /\p{Letter}|\p{Number}/u.test(String.fromCodePoint(first));
  return (plain && first !== 35 ? '# ' : '') + c.name + (c.can_write === false ? ' · нет прав' : '');
}

/* вторая строка в списке каналов: где живёт и чем отличается, чтобы не гадать по имени */
function chanSub(c) {
  const bits = [];
  if (c.category) bits.push('📂 ' + c.category);
  if (c.kind && c.kind !== 'канал') bits.push(c.kind);
  if (c.slow) bits.push('не чаще, чем раз в ' + c.slow + ' с');
  if (c.nsfw) bits.push('18+');
  if (c.topic) bits.push(c.topic);
  return bits.join(' · ');
}

function memSub(m) {
  const bits = ['ID ' + m.id];
  if (m.voice) bits.push('🎙 ' + m.voice);
  if (m.joined) bits.push('в составе с ' + m.joined);
  return bits.join(' · ');
}

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
  const chLine = h('div', { class: 'pickline' });
  let channelId = '';
  let chanRows = [];

  function sayChannel() {
    const c = chanRows.find((x) => x.id === channelId);
    chLine.replaceChildren(h('span', null, c ? 'Пишем в ' : 'Канал не выбран — нажми на строчку.'));
    if (c) chLine.append(h('b', null, chanLabel(c)));
    if (c && c.can_write === false) chLine.append(chip('нет прав писать', 'err'));
    if (c && c.is_review) chLine.append(chip('канал заявок: только про набор', 'err'));
  }

  async function loadChannels() {
    const d = await api('/api/channels?q=' + encodeURIComponent(cq.value.trim()));
    chanRows = d.rows;
    chBox.replaceChildren(...d.rows.map((c) => h('label', null,
      h('input', { type: 'radio', name: 'chan', value: c.id, checked: c.id === channelId,
        onchange: () => { channelId = c.id; sayChannel(); } }),
      h('span', { class: 'grow' }, h('b', null, chanLabel(c)), h('small', null, chanSub(c))),
      c.can_write === false ? chip('нет прав', 'err') : (c.is_review ? chip('заявки', 'err') : null)
    )));
    if (!d.rows.length) chBox.append(h('div', { class: 'none' }, 'Не нашлось ни одного канала.'));
    sayChannel();
  }

  const mq = h('input', { type: 'search', placeholder: 'Ник или ID…' });
  const mBox = h('div', { class: 'picks' });
  const mLine = h('div', { class: 'pickline' });
  let dmUser = '';
  let memRows = [];
  const chosen = new Set();

  function sayMembers() {
    const m = memRows.find((x) => x.id === dmUser);
    mLine.replaceChildren(h('span', null, 'В ЛС одному: '), h('b', null, m ? m.name : 'не выбран'),
      h('span', null, '· в рассылку попало '), h('b', null, chosen.size + ' чел.'));
  }

  async function loadMembers() {
    const d = await api('/api/members?q=' + encodeURIComponent(mq.value.trim()) + '&limit=120');
    memRows = d.rows;
    mBox.replaceChildren(...d.rows.map((m) => {
      const check = h('input', { type: 'checkbox', checked: chosen.has(m.id), value: m.id,
        onchange: (e) => { e.target.checked ? chosen.add(m.id) : chosen.delete(m.id); sayMembers(); } });
      const one = h('label', null,
        h('input', { type: 'radio', name: 'dmuser', value: m.id, checked: m.id === dmUser,
          onchange: () => { dmUser = m.id; sayMembers(); } }),
        check,
        h('span', { class: 'grow' }, h('b', null, m.name), h('small', null, memSub(m))),
        chip(m.top || 'без роли', m.top ? 'ok' : 'off'));
      return one;
    }));
    if (!d.rows.length) mBox.append(h('div', { class: 'none' }, 'Никого не нашлось.'));
    sayMembers();
  }

  const mark = h('small', { class: 'muted' }, '');
  const upd = () => { mark.textContent = 'Отмечено: ' + chosen.size; sayMembers(); };
  const allBtn = h('button', { class: 'btn', onclick: () => {
    memRows.forEach((m) => chosen.add(m.id));
    loadMembers(); upd();
  } }, 'Отметить всех в списке');
  const noBtn = h('button', { class: 'btn', onclick: () => {
    chosen.clear();
    loadMembers(); upd();
  } }, 'Снять всех');

  const f1 = msgForm(true), r1 = resultBox();
  f1.box.append(h('button', { class: 'btn primary', onclick: async () => {
    r1.busy();
    try { r1.show(await api('/api/send', { method: 'POST', body: { channel: channelId, text: f1.text.value, title: f1.title.value, color: f1.color.value, ping: f1.ping.checked } })); }
    catch (e) { r1.err(e); }
  } }, 'Отправить в канал'), h('p', { class: 'muted small' }, 'Сначала выбери канал кружком в списке.'));

  const f2 = msgForm(false), r2 = resultBox();
  f2.box.append(h('button', { class: 'btn primary', onclick: async () => {
    r2.busy();
    try { r2.show(await api('/api/dm', { method: 'POST', body: { user: dmUser, text: f2.text.value, title: f2.title.value, color: f2.color.value } })); }
    catch (e) { r2.err(e); }
  } }, 'Отправить в ЛС'));

  const f3 = msgForm(false), r3 = resultBox();
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
    pageHead('Сообщения', 'Панель пишет от имени бота: в канал, в ЛС и рассылкой по составу.',
      h('button', { class: 'btn', onclick: () => go('send') }, 'Обновить')),
    h('div', { class: 'grid2' },
      h('div', { class: 'card form' }, h('h2', null, '✉️ В канал'),
        h('p', { class: 'muted small' }, 'Кружок слева — какой канал выбран. Серым помечено, куда бот писать не может.'),
        h('label', null, 'Поиск канала', cq), chBox, chLine, f1.box, r1),
      h('div', { class: 'card form' }, h('h2', null, '👤 В ЛС и рассылкой'),
        h('p', { class: 'muted small' }, 'Кружок — написать одному. Галочка — взять в рассылку. Можно и то, и другое.'),
        h('label', null, 'Кого', mq), mBox, mLine,
        h('div', { class: 'row' }, allBtn, noBtn),
        h('div', { class: 'section-title' }, 'Одному'), f2.box, r2,
        h('div', { class: 'section-title' }, 'Рассылка по галочкам'), f3.box, r3)
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
    .map((c) => h('option', { value: c.id }, chanLabel(c))));
  const out = h('div', { class: 'card' }, h('p', { class: 'muted' }, 'Открой экран — его ответ появится здесь, как в Discord.'));
  const refresh = (res) => out.replaceChildren(screenNode(res, actorSel, refresh));
  const filter = h('input', { type: 'search', placeholder: 'Найти экран по названию…' });

  const run = (x, args) => {
    out.replaceChildren(spinner('Экран думает…'));
    api('/api/commands/run', { method: 'POST', body: { ref: x.ref, actor: actorSel.value, channel: chanSel.value, args: args || {} } })
      .then(refresh).catch((e) => out.replaceChildren(h('p', { class: 'err' }, e.message)));
  };

  /* ── поля формы: экраны с аргументами раньше жили только в Discord ── */
  let people = null, chanels = null;
  async function optionsOf(kind) {
    if (kind === 'member') {
      if (!people) people = (await api('/api/members?limit=200')).rows;
      return people.map((m) => [m.id, m.name + (m.top ? ' · ' + m.top : '')]);
    }
    if (!chanels) chanels = chans.rows;
    return chanels.map((c) => [c.id, chanLabel(c) + (c.category ? ' · ' + c.category : '')]);
  }

  async function pickerNode(kind, store, name) {
    const q = h('input', { type: 'search', placeholder: 'фильтр по имени или ID…' });
    const sel = h('select', { size: '5' });
    let all = [];
    try { all = await optionsOf(kind); } catch (e) { /* список пустой, панель скажет ошибкой */ }
    const fill = (needle) => {
      const rows = needle ? all.filter(([id, label]) => label.toLowerCase().includes(needle) || id.includes(needle)) : all;
      sel.replaceChildren(...(rows.length ? rows.slice(0, 150) : [['', 'никого не найдено']])
        .map(([id, label]) => h('option', { value: id }, label)));
      store[name] = () => sel.value;
    };
    q.addEventListener('input', () => fill(q.value.trim().toLowerCase()));
    fill('');
    return h('div', { class: 'form' }, q, sel);
  }

  async function openForm(x) {
    const store = {};
    const fields = h('div', { class: 'form' });
    out.replaceChildren(h('h2', null, (x.icon || '') + ' ' + x.label),
      h('p', { class: 'muted small' }, 'Экрану нужны аргументы — как в слэш-команде ' + x.cmd + '. Заполни и открой.'),
      fields,
      h('div', { class: 'row' }, h('button', {
        class: 'btn primary', onclick: () => {
          const args = {};
          Object.entries(store).forEach(([k, get]) => { args[k] = get(); });
          run(x, args);
        }
      }, 'Открыть экран'), h('button', { class: 'btn', onclick: () => renderNav() }, 'Назад')));
    for (const f of x.args) {
      let node;
      if (f.choices && f.choices.length) {
        const sel = h('select', null, f.choices.map(([label, value]) => h('option', { value: value }, label)));
        store[f.name] = () => sel.value;
        node = sel;
      } else if (f.kind === 'bool') {
        const box = h('input', { type: 'checkbox' });
        store[f.name] = () => (box.checked ? 'true' : 'false');
        node = h('div', { class: 'row' }, box, h('span', { class: 'muted small' }, 'галочка = да'));
      } else if (f.kind === 'member' || f.kind === 'channel') {
        node = await pickerNode(f.kind, store, f.name);
      } else {
        const inp = h('input', { type: f.kind === 'number' ? 'number' : 'text', placeholder: f.label || f.name });
        store[f.name] = () => inp.value;
        node = inp;
      }
      fields.append(h('label', null, f.label || f.name, node));
    }
  }

  const nav = h('div', { class: 'grid2' });
  /* Избранное живёт в браузере: у каждого свои «главные» экраны, и они первыми. */
  const allScreens = [];
  d.sections.forEach((s) => s.screens.forEach((x) => allScreens.push(Object.assign({ section: s.title }, x))));
  const byRef = {};
  allScreens.forEach((x) => { byRef[x.ref] = x; });
  let favs = favGet();

  function screenBtn(x) {
    const needs = x.args && x.args.length;
    const fav = favs.indexOf(x.ref) >= 0;
    const star = h('button', {
      class: 'btn star' + (fav ? ' on' : ''), title: fav ? 'Убрать из главных' : 'Закрепить как главный экран',
      onclick: (ev) => {
        ev.stopPropagation();
        favToggle(x.ref);
        favs = favGet();
        renderNav();
      }
    }, fav ? '★' : '☆');
    return h('span', { class: 'pinwrap' }, h('button', {
      class: 'btn' + (x.live === false ? ' dim' : ''),
      title: x.cmd + (x.live === false ? ' · ког выключен, экран не откроется' : '')
        + (needs ? ' · ' + x.args.map((f) => f.label).join(', ') : ''),
      onclick: () => (needs ? openForm(x) : run(x))
    }, (x.icon || '') + ' ' + x.label + (needs ? ' ✎' : '')), star);
  }

  function renderNav() {
    const needle = filter.value.trim().toLowerCase();
    const cards = [];
    const pinned = favGet().map((r) => byRef[r]).filter(Boolean);
    if (pinned.length && !needle) {
      cards.push(h('div', { class: 'card form pinned' },
        h('h2', null, '⭐ Главное'),
        h('p', { class: 'muted small' }, 'Твои закреплённые экраны — звёздочкой на любой кнопке.'),
        h('div', { class: 'btns' }, pinned.map(screenBtn))));
    }
    d.sections.forEach((s) => {
      const items = s.screens.filter((x) => !needle
        || (x.label + ' ' + x.cmd + ' ' + s.title).toLowerCase().includes(needle));
      if (!items.length) return;
      cards.push(h('div', { class: 'card form' },
        h('h2', null, (s.icon || '') + ' ' + s.title + ' · ' + items.length),
        s.only_web ? h('p', { class: 'muted small' }, 'Им нужны аргументы: панель спросит формой, в Discord они остаются своими командами.') : null,
        h('div', { class: 'btns' }, items.map(screenBtn))));
    });
    nav.replaceChildren(...cards);
    if (!cards.length) nav.replaceChildren(h('p', { class: 'muted' }, 'Ничего не нашлось по «' + filter.value + '».'));
  }
  filter.addEventListener('input', renderNav);
  renderNav();

  const ready = d.sections.reduce((n, s) => n + s.screens.filter((x) => x.live !== false).length, 0);
  const total = d.sections.reduce((n, s) => n + s.screens.length, 0);
  return h('div', null,
    pageHead('Экраны команд', 'Команды бота работают отсюда: экран получает тот же ответ, что и в Discord, и его кнопки живые.',
      h('button', { class: 'btn', onclick: () => go('screens') }, 'Обновить')),
    h('div', { class: 'toolbar' }, h('label', null, 'Найти', filter),
      h('label', null, 'От чьего имени', actorSel), h('label', null, 'Куда отвечать', chanSel),
      chip(ready + ' из ' + total + ' экранов открыты', ready === total ? 'ok' : 'off'),
      d.hidden && d.hidden.length ? chip('спрятано дублей: ' + d.hidden.length, 'off') : null),
    h('div', { class: 'split', style: 'grid-template-columns:minmax(300px,46%) 1fr' }, nav,
      h('div', { class: 'stick' }, h('div', { class: 'section-title' }, 'Ответ'), out))
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
  applyTheme();
  state.botName = s.bot_name || 'Бот';
  document.title = state.botName + ' · Панель';
  renderShell();
  go(location.hash.slice(1) || 'overview');
}

function skeleton() {
  /* Скелетон вместо «Загрузка…»: экран не прыгает, когда данные приходят. */
  const box = h('div', { class: 'skelwrap' });
  box.append(h('div', { class: 'skel', style: 'height:26px;width:38%' }));
  for (let i = 0; i < 5; i++) {
    box.append(h('div', { class: 'skelrow' }, h('div', { class: 'skel', style: 'width:' + (34 + (i % 3) * 18) + '%' }),
      h('div', { class: 'skel', style: 'width:' + (18 + (i % 2) * 22) + '%' })));
  }
  return box;
}

window.addEventListener('beforeunload', (e) => { if (state.isDirty()) { e.preventDefault(); e.returnValue = ''; } });
applyTheme();
boot();
})();
</script>
</body>
</html>
"""
