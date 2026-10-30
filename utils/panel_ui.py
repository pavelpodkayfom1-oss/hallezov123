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
<meta name="theme-color" content="#000000">
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
.picks label{display:flex;flex-direction:row;gap:.6rem;align-items:center;text-align:left;padding:.45rem .55rem;border-radius:9px;
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

/* ============================================================
   Чёрно-белая палитра и экран входа в стиле HUD.
   Красного больше нет: акцент белый (в светлой теме чёрный).
   ============================================================ */
:root{
  --bg:#000; --bg2:#050505;
  --panel:#0a0a0a; --panel2:#111; --panel3:#1a1a1a;
  --line:rgba(255,255,255,.10); --line2:rgba(255,255,255,.22);
  --text:#f4f4f4; --muted:#9a9a9a; --faint:#5f5f5f;
  --accent:#fff; --accent2:#d8d8d8; --accent-d:#bbb; --on-accent:#000;
  --accent-soft:rgba(255,255,255,.07); --accent-line:rgba(255,255,255,.4);
  --field:#050505; --code:#000; --hover:rgba(255,255,255,.06);
}
html[data-theme=light]{
  --bg:#f5f5f5; --bg2:#fff;
  --panel:#fff; --panel2:#f1f1f1; --panel3:#e6e6e6;
  --line:rgba(0,0,0,.12); --line2:rgba(0,0,0,.25);
  --text:#0a0a0a; --muted:#666; --faint:#999;
  --accent:#000; --accent2:#333; --accent-d:#222; --on-accent:#fff;
  --accent-soft:rgba(0,0,0,.06); --accent-line:rgba(0,0,0,.45);
  --field:#fff; --code:#0a0a0a; --hover:rgba(0,0,0,.05);
}
body::before{background:
  radial-gradient(1000px 560px at 50% -14%, rgba(255,255,255,.11), transparent 70%),
  radial-gradient(760px 520px at 100% 112%, rgba(255,255,255,.055), transparent 70%),
  radial-gradient(700px 480px at 0% 100%, rgba(255,255,255,.035), transparent 70%),
  var(--bg)}
body::after{
  background-image:
    linear-gradient(rgba(255,255,255,.05) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.05) 1px,transparent 1px);
  background-size:56px 56px;
  -webkit-mask-image:radial-gradient(ellipse 85% 75% at 50% 0%,#000,transparent 80%);
  mask-image:radial-gradient(ellipse 85% 75% at 50% 0%,#000,transparent 80%)}
html[data-theme=light] body::before{background:
  radial-gradient(1000px 560px at 50% -14%, rgba(0,0,0,.06), transparent 70%),
  radial-gradient(760px 520px at 100% 112%, rgba(0,0,0,.04), transparent 70%),
  var(--bg)}
html[data-theme=light] body::after{
  background-image:
    linear-gradient(rgba(0,0,0,.06) 1px,transparent 1px),
    linear-gradient(90deg,rgba(0,0,0,.06) 1px,transparent 1px)}
::selection{background:var(--accent);color:var(--on-accent)}
a{color:var(--text)}
input:focus,select:focus,textarea:focus{border-color:var(--accent-line);box-shadow:0 0 0 3px var(--accent-soft)}
.btn.primary,html[data-theme=light] .btn.primary{background:var(--accent);color:var(--on-accent);border-color:transparent}
.btn.primary:hover{background:var(--accent2)}
.logo{background:var(--accent);color:var(--on-accent)}
.side{background:rgba(0,0,0,.9)}
html[data-theme=light] .side{background:rgba(255,255,255,.95)}
.nav button.active{background:var(--accent-soft);color:var(--text)}
.nav button.active .ico{background:var(--accent);color:var(--on-accent)}
.nav button.active::before{background:var(--accent)}
.pager button.active{background:var(--accent);color:var(--on-accent)}
.switch.on{background:var(--accent)}
.switch.on::after{background:var(--on-accent)}
input[type=checkbox]:checked,input[type=radio]:checked{background:var(--accent);box-shadow:none}
input[type=checkbox]:checked::after{border-color:var(--on-accent)}
input[type=radio]:checked::after{background:var(--on-accent)}
.stat::before{background:var(--accent)}
.stat.ok::before{background:var(--ok)} .stat.warn::before{background:var(--warn)}
.bar i{background:var(--accent)}
.bar i.warn{background:var(--warn)} .bar i.ok{background:var(--ok)}
.r.sel,.files button.active,.picks label:has(input:checked){background:var(--accent-soft);border-color:var(--accent-line)}
.note{border-color:var(--line2);background:var(--accent-soft)}
.tile .ic{background:var(--accent-soft);border-color:var(--line2)}
@media (max-width:1000px){
  .side{background:var(--panel)}
  .mbar{background:rgba(0,0,0,.95)}
  html[data-theme=light] .mbar{background:rgba(255,255,255,.96)}
}

/* ---------- вход в стиле HUD ---------- */
.hudwrap{position:relative;min-height:100vh;min-height:100dvh;display:grid;place-items:center;padding:1.2rem;overflow:hidden}
.hudwrap::before{content:"";position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(closest-side at 50% 40%,rgba(255,255,255,.13),transparent 72%)}
html[data-theme=light] .hudwrap::before{background:radial-gradient(closest-side at 50% 40%,rgba(0,0,0,.08),transparent 72%)}
.hudwrap::after{content:"";position:absolute;left:0;right:0;top:-180px;height:180px;pointer-events:none;
  background:linear-gradient(180deg,transparent,rgba(255,255,255,.05),transparent);animation:scan 9s linear infinite}
html[data-theme=light] .hudwrap::after{background:linear-gradient(180deg,transparent,rgba(0,0,0,.045),transparent)}
@keyframes scan{to{transform:translateY(calc(100vh + 180px))}}
.hudgrid{position:absolute;inset:0;pointer-events:none;opacity:.9;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='48' height='48'%3E%3Cpath d='M24 20v8M20 24h8' stroke='white' stroke-opacity='.22' fill='none'/%3E%3C/svg%3E");
  -webkit-mask-image:radial-gradient(ellipse 70% 62% at 50% 46%,#000,transparent 80%);mask-image:radial-gradient(ellipse 70% 62% at 50% 46%,#000,transparent 80%)}
html[data-theme=light] .hudgrid{
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='48' height='48'%3E%3Cpath d='M24 20v8M20 24h8' stroke='black' stroke-opacity='.25' fill='none'/%3E%3C/svg%3E")}
.tele{position:absolute;font:.62rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.24em;text-transform:uppercase;color:var(--faint)}
.tele.tl{top:1.4rem;left:1.6rem}.tele.tr{top:1.4rem;right:1.6rem}
.tele.bl{bottom:1.4rem;left:1.6rem}.tele.br{bottom:1.4rem;right:1.6rem}
.tele.tl::before,.tele.bl::before{content:"";display:inline-block;width:18px;height:1px;background:var(--faint);vertical-align:middle;margin-right:.8em}
.tele.tr::after,.tele.br::after{content:"";display:inline-block;width:18px;height:1px;background:var(--faint);vertical-align:middle;margin-left:.8em}

.hud{position:relative;z-index:1;width:100%;max-width:440px;display:flex;flex-direction:column;gap:.75rem;
  padding:1.5rem 2.2rem 1.2rem;text-align:center;border:1px solid var(--line);background:rgba(0,0,0,.55);
  transition:transform .6s cubic-bezier(.2,.7,.3,1),opacity .6s;animation:hudin .55s ease-out both}
html[data-theme=light] .hud{background:rgba(255,255,255,.75)}
@keyframes hudin{from{opacity:0;transform:scale(.97)}to{opacity:1;transform:none}}
.hud .c{position:absolute;width:20px;height:20px;border:2px solid var(--text);pointer-events:none;transition:border-color .2s,width .3s,height .3s}
.hud .c.tl{top:-2px;left:-2px;border-right:0;border-bottom:0}
.hud .c.tr{top:-2px;right:-2px;border-left:0;border-bottom:0}
.hud .c.bl{bottom:-2px;left:-2px;border-right:0;border-top:0}
.hud .c.br{bottom:-2px;right:-2px;border-left:0;border-top:0}
.hud:focus-within .c{width:28px;height:28px}

.reactor{width:220px;height:220px;margin:0 auto;color:var(--text);filter:drop-shadow(0 0 14px rgba(255,255,255,.35))}
html[data-theme=light] .reactor{filter:none}
.reactor svg{width:100%;height:100%;display:block;overflow:visible}
.rg{transform-box:view-box;transform-origin:110px 110px}
.r1{animation:spin 40s linear infinite}
.r2{animation:spin 22s linear infinite reverse}
.r3{animation:spin 13s linear infinite}
.r4{animation:spin 9s linear infinite reverse}
@keyframes spin{to{transform:rotate(360deg)}}
.reactor .core{animation:corepulse 3.2s ease-in-out infinite;transform-box:view-box;transform-origin:110px 110px}
.reactor .core2{opacity:.95}
@keyframes corepulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.6;transform:scale(.92)}}
.hud.checking .r1{animation-duration:4s}.hud.checking .r2{animation-duration:3s}
.hud.checking .r3{animation-duration:2s}.hud.checking .r4{animation-duration:1.4s}
.hudcap{font:600 .68rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.34em;text-transform:uppercase;color:var(--muted);padding-left:.34em}
.hud h1{margin:0;font-size:1.3rem;font-weight:600;letter-spacing:.16em;text-transform:uppercase}

.hudlog{display:flex;flex-direction:column;gap:.3rem;margin:.2rem 0 .1rem;text-align:left;
  font:.68rem/1.2 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.hudlog>div{display:flex;align-items:center;gap:.5em;opacity:0;animation:logline .35s ease-out forwards}
.hudlog i{flex:1;border-bottom:1px dotted var(--line2);transform:translateY(2px)}
.hudlog b{font-weight:600;color:var(--text)}
.hudlog>div:last-child b{color:var(--muted)}
@keyframes logline{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}

.hudfield{display:block;margin-top:.4rem;position:relative}
.hudfield::after{content:"";position:absolute;left:50%;bottom:0;height:1px;width:0;background:var(--text);
  transform:translateX(-50%);transition:width .3s cubic-bezier(.2,.7,.3,1)}
.hudfield:focus-within::after{width:100%}
.hudfield input{width:100%;background:transparent;border:0;border-bottom:1px solid var(--line2);border-radius:0;outline:0;
  text-align:center;letter-spacing:.45em;padding:.7em 0 .7em .45em;font:1.1rem ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;box-shadow:none}
.hudfield input:focus{box-shadow:none;border-bottom-color:var(--line2)}
.hudfield input::placeholder{letter-spacing:.3em;color:var(--faint)}
.hudbtn{position:relative;overflow:hidden;border:1px solid var(--text);background:transparent;color:var(--text);padding:.85em 1em;cursor:pointer;
  font:600 .78rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.32em;text-transform:uppercase;
  transition:background .15s,color .15s}
.hudbtn span{position:relative;z-index:1;padding-left:.32em}
.hudbtn:hover:not(:disabled){background:var(--text);color:var(--bg)}
.hudbtn:focus-visible{outline:2px solid var(--text);outline-offset:3px}
.hudbtn:disabled{cursor:wait}
.hud.checking .hudbtn::after{content:"";position:absolute;top:0;bottom:0;width:40%;left:-40%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.3),transparent);animation:sweepbtn 1s linear infinite}
@keyframes sweepbtn{to{left:110%}}
.hudstat{font:.72rem/1.2 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);min-height:1em}
.hudstat::after{content:"_";animation:blink 1s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
.hud .hud-err{margin:0;min-height:1.2em;font:.74rem/1.3 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.06em;color:var(--err)}
.hudfoot{display:flex;justify-content:space-between;margin-top:.2rem;padding-top:.65rem;border-top:1px solid var(--line);
  font:.64rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.18em;text-transform:uppercase;color:var(--faint)}
.hudfoot span:first-child::before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--ok);margin-right:.6em;vertical-align:1px}
.hud.deny{animation:shake .4s}
.hud.deny .c{border-color:var(--err)}
.hud.deny .hudstat{color:var(--err)}
@keyframes shake{20%{transform:translateX(-9px)}40%{transform:translateX(8px)}60%{transform:translateX(-5px)}80%{transform:translateX(3px)}}
.hud.granted .reactor{filter:drop-shadow(0 0 30px rgba(255,255,255,.95))}
.hud.granted .r1,.hud.granted .r2,.hud.granted .r3,.hud.granted .r4{animation-duration:1s}
.hud.granted .hudstat{color:var(--text)}
.hud.granted{transform:scale(1.04);opacity:0;transition-delay:.45s}
.hudflash{position:absolute;inset:0;background:#fff;opacity:0;pointer-events:none;z-index:5}
.hudwrap.granted .hudflash{animation:flash .9s ease-out .35s forwards}
@keyframes flash{0%{opacity:0}35%{opacity:.85}100%{opacity:0}}
@media (max-width:560px){
  .tele{display:none}
  .hud{padding:1.3rem 1.2rem 1rem}
  .reactor{width:176px;height:176px}
  .hud h1{font-size:1.08rem}
}
@media (max-height:700px){.reactor{width:150px;height:150px}.hudlog{display:none}}
@media (prefers-reduced-motion:reduce){
  .hudwrap::after,.reactor *,.hud,.hudlog>div,.hudflash{animation:none!important}
  .hudlog>div{opacity:1}
}

/* ---------- эмодзи, предпросмотр в стиле Discord, тексты ---------- */
img.emo{width:1.3em;height:1.3em;vertical-align:-.3em;object-fit:contain;display:inline-block}
.embed div,.embed b{white-space:pre-wrap;word-break:break-word}
.epick{max-width:640px}
.egrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(82px,1fr));gap:.35rem;padding:.6rem;max-height:52vh;overflow:auto}
.egrid .none,.egrid>p{grid-column:1/-1;padding:1rem;color:var(--muted);text-align:center}
.ebtn{display:flex;flex-direction:column;align-items:center;gap:.3rem;padding:.55rem .3rem;background:none;border:1px solid transparent;
  border-radius:10px;color:var(--text);cursor:pointer}
.ebtn:hover{background:var(--hover);border-color:var(--line2)}
.ebtn img{width:40px;height:40px;object-fit:contain}
.ebtn span{font-size:.66rem;color:var(--muted);max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ebtn.off img{opacity:.38;filter:grayscale(1)}
.efoot{padding:.65rem 1rem;border-top:1px solid var(--line);font-size:.78rem;color:var(--muted)}
.egallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(148px,1fr));gap:.8rem}
.ecard{display:flex;flex-direction:column;align-items:center;gap:.5rem;text-align:center;padding:.9rem .7rem}
.ecard b{font-size:.85rem;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ecard.off .eimg img{opacity:.4;filter:grayscale(1)}
.eimg{background:var(--hover);border:0;border-radius:14px;padding:.6rem;cursor:pointer;display:grid;place-items:center}
.eimg img{width:64px;height:64px;object-fit:contain;transition:transform .15s}
.eimg:hover img{transform:scale(1.12)}
.tsplit{grid-template-columns:minmax(290px,370px) 1fr!important;align-items:start}
.tside{position:sticky;top:1.2rem;display:flex;flex-direction:column;gap:.7rem;max-height:calc(100vh - 2.4rem)}
.tsearch input{width:100%}
.tfilters{display:grid;grid-template-columns:1fr 1fr;gap:.45rem}
.tfilters select{width:100%;min-width:0;font-size:.82rem;padding-top:.45em;padding-bottom:.45em}
.chipbtn{grid-column:1/-1;background:var(--panel2);border:1px solid var(--line);color:var(--muted);border-radius:999px;padding:.4em .9em;font-size:.8rem;cursor:pointer}
.chipbtn:hover{color:var(--text)} .chipbtn.on{background:var(--accent);color:var(--on-accent,#000);border-color:var(--accent)}
.tcount{margin-top:-.2rem}
.tscroll{flex:1}
.tscroll{overflow:auto;min-height:0;padding-right:.2rem}
.tlist{display:flex;flex-direction:column;gap:.4rem}
.tlist .section-title{margin:.9rem 0 .2rem}
.tlist .section-title:first-child{margin-top:.1rem}
.ted{display:flex;flex-direction:column;gap:.8rem;min-width:0}
.thead{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap}
.thead h2{margin:0 0 .15rem}
.ttools{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}
.ttools select{padding-top:.3em;padding-bottom:.3em;font-size:.82rem}
.head .rfctl{flex:none;flex-wrap:nowrap}
.rfctl select{max-width:15rem;min-width:0;padding-top:.3em;padding-bottom:.3em;font-size:.82rem}
.meter{height:4px;border-radius:4px;background:var(--panel3);overflow:hidden;margin-top:-.4rem}
.meter i{display:block;height:100%;width:0;background:var(--accent);transition:width .2s}
.meter.warn i{background:var(--warn)} .meter.bad i{background:var(--err)}
.ted textarea.msg.tall{min-height:200px}
.actbar{position:sticky;bottom:0;display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;padding:.8rem 0 .2rem;margin-top:.2rem;
  border-top:1px solid var(--line);background:linear-gradient(180deg,transparent,var(--panel) 40%)}
.dcprev{display:flex;gap:.8rem;padding:1rem;border-radius:12px;background:#313338;color:#dbdee1;text-align:left}
html[data-theme=light] .dcprev{background:#313338}
.dcava{flex:none;width:40px;height:40px;border-radius:50%;background:#5865f2;color:#fff;display:grid;place-items:center;font-weight:700}
.dcbody{min-width:0;flex:1}
.dchead{display:flex;align-items:center;gap:.4rem;margin-bottom:.3rem}
.dchead b{color:#fff;font-size:.95rem}
.dctag{background:#5865f2;color:#fff;font-size:.58rem;font-weight:700;padding:.12em .4em;border-radius:4px}
.dcprev .embed{background:#2b2d31;border-left:4px solid #fff;margin:0;max-width:520px;color:#dbdee1}
.dcprev .embed b{color:#fff}
.dcbtn{display:inline-block;background:#4e5058;color:#fff;border-radius:6px;padding:.45em 1em;font-size:.88rem;font-weight:500}
.dcfield label{display:block;color:#b5bac1;font-size:.75rem;font-weight:700;text-transform:uppercase;margin-bottom:.35rem}
.dcinput{background:#1e1f22;border-radius:6px;padding:.6em .8em;color:#949ba4;font-size:.9rem}
.dctopic{border-left:2px solid #4e5058;padding-left:.6rem;color:#b5bac1;font-size:.86rem}
.tlist .r small.src{opacity:.72;font-size:.72rem}
@media (max-width:1000px){.tsplit{grid-template-columns:1fr!important}.tside{position:static;max-height:none}.tscroll{max-height:42vh}}

/* ---------- поиск, карточка ошибки, проверка JSON ---------- */
.searchbtn{display:flex;align-items:center;gap:.6rem;width:100%;margin:0 0 .4rem;padding:.55em .7em;background:var(--panel);
  border:1px solid var(--line);border-radius:10px;color:var(--muted);cursor:pointer;font-size:.88rem;text-align:left}
.searchbtn:hover{border-color:var(--line2);color:var(--text)}
.searchbtn .ico{display:grid;place-items:center}
.searchbtn kbd{margin-left:auto;font:.66rem ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:.15em .45em;
  border:1px solid var(--line2);border-radius:6px;color:var(--faint)}
dialog.pal{padding:0;max-width:560px;margin:12vh auto auto;overflow:hidden}
.pal input{width:100%;border:0;border-bottom:1px solid var(--line);border-radius:0;padding:1em 1.1em;font-size:1rem;background:transparent;box-shadow:none}
.pal input:focus{box-shadow:none;border-color:var(--line)}
.palist{max-height:50vh;overflow:auto;padding:.4rem}
.paitem{display:flex;align-items:center;gap:.7rem;width:100%;padding:.6em .7em;background:none;border:0;border-radius:9px;
  color:var(--text);cursor:pointer;text-align:left;font-size:.93rem}
.paitem .grow{flex:1}
.paitem .ico{display:grid;place-items:center;color:var(--muted)}
.paitem small{color:var(--faint);font-size:.7rem;text-transform:uppercase;letter-spacing:.1em}
.paitem.on,.paitem:hover{background:var(--hover)}
.pal .none{padding:1rem;color:var(--muted);text-align:center}
.errcard{max-width:660px}
.errdetail{margin:.6rem 0 1rem;padding:.75rem .9rem;background:var(--code);color:#ffb3b8;border:1px solid var(--line);
  border-radius:9px;white-space:pre-wrap;word-break:break-word;font-size:.8rem}
html[data-theme=light] .errdetail{color:#ff9aa0}
.ok-text{color:var(--ok)} .err-text{color:var(--err)}

.searchbtn span{flex:1;white-space:nowrap}
.searchbtn kbd{white-space:nowrap}
.hudfield input::selection{background:rgba(255,255,255,.28);color:inherit}
html[data-theme=light] .hudfield input::selection{background:rgba(0,0,0,.2)}
.embed strong,.dcprev strong{font-weight:700}

.tsplit>*,.split>*,.cols>*,.grid2>*{min-width:0}
.tside,.ted,.tsearch{max-width:100%}
.drop{border:2px dashed var(--line);border-radius:var(--radius);padding:1.15rem;text-align:center;
  color:var(--muted);cursor:pointer;transition:border-color .16s,background .16s,color .16s}
.drop:hover,.drop.over{border-color:var(--accent2);background:rgba(255,255,255,.035);color:var(--text)}
.drop b{color:var(--text)}
.shots{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.6rem}
.shots figure{position:relative;margin:0}
.shots img{width:98px;height:98px;object-fit:cover;border-radius:10px;border:1px solid var(--line);display:block}
.shots figcaption{font-size:.7rem;color:var(--muted);text-align:center;margin-top:.15rem}
.shots button{position:absolute;top:-7px;right:-7px;width:22px;height:22px;border-radius:50%;
  border:1px solid var(--line);background:var(--panel2);color:var(--text);cursor:pointer;line-height:1;padding:0}

/* ---------- пульс, заметки, планировщик ---------- */
.pulsebox{padding:1rem 1.1rem .9rem;margin-top:1.1rem}
.pulsehead{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;flex-wrap:wrap;margin-bottom:.5rem}
.pulsehead h2{margin:0;font-size:1.02rem}
.pulse{display:block;width:100%;height:150px}
.pulsecap{display:flex;gap:1.4rem;flex-wrap:wrap;margin-top:.5rem;font:.72rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.pulsecap b{color:var(--text);font-weight:600;margin-left:.3em}
.composer{margin-bottom:1rem}
.row.between{justify-content:space-between;align-items:center}
.notebar{margin:.4rem 0 1rem;gap:1rem}
.notebar input{max-width:340px}
.notes{columns:3 260px;column-gap:.9rem}
.note-c{break-inside:avoid;margin:0 0 .9rem;padding:.95rem 1rem .6rem;display:flex;flex-direction:column;gap:.55rem;border-left:3px solid var(--line2)}
.note-c.pinned{border-color:var(--line2);border-left-color:var(--text)}
.note-c.t-white{border-left-color:#fff}
.note-c.t-ok{border-left-color:var(--ok)}
.note-c.t-warn{border-left-color:var(--warn)}
.note-c.t-bad{border-left-color:var(--err)}
.nbody{white-space:pre-wrap;word-break:break-word;line-height:1.5}
.nbody textarea{width:100%;min-height:120px}
.note-c footer{display:flex;align-items:center;justify-content:space-between;gap:.5rem;border-top:1px solid var(--line);padding-top:.4rem}
.nact{display:flex;gap:.1rem;opacity:.55;transition:opacity .15s}
.note-c:hover .nact,.note-c:focus-within .nact{opacity:1}
.ibtn{background:none;border:0;color:var(--text);cursor:pointer;width:30px;height:30px;border-radius:8px;font-size:.95rem;line-height:1}
.ibtn:hover{background:var(--hover)}
.ibtn.on{color:var(--warn)}
.tones{display:flex;gap:.45rem}
.tone{width:22px;height:22px;border-radius:50%;border:2px solid var(--line2);cursor:pointer;padding:0;background:var(--panel3)}
.tone.t-white{background:#fff}.tone.t-ok{background:var(--ok)}.tone.t-warn{background:var(--warn)}.tone.t-bad{background:var(--err)}
.tone.on{box-shadow:0 0 0 2px var(--bg),0 0 0 4px var(--text)}
.empty{padding:1.4rem;text-align:center;color:var(--muted)}
.plist{display:flex;flex-direction:column;gap:.6rem}
.plitem{display:grid;grid-template-columns:120px 1fr auto;gap:.9rem;align-items:start;padding:.85rem 1rem}
.plwhen{display:flex;flex-direction:column;gap:.15rem}
.plmain{min-width:0}
.plsnip{margin:.35rem 0 0;white-space:pre-wrap;word-break:break-word;color:var(--muted)}
.plitem.sent,.plitem.fail{opacity:.8}
.plitem.fail{border-left:3px solid var(--err)}
@media (max-width:640px){.plitem{grid-template-columns:1fr auto}.plwhen{grid-column:1/-1;flex-direction:row;gap:.6rem;align-items:baseline}}

/* вход: приветствие, шкала заряда, слежение за курсором */
.hudsay{min-height:1.2em;font:.74rem/1.3 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--text)}
.hudsay::after{content:"▌";margin-left:.2em;animation:blink 1s steps(1) infinite;color:var(--muted)}
.hudmeter{display:grid;grid-template-columns:repeat(14,1fr);gap:3px;height:6px;margin-top:.15rem}
.hudmeter i{background:var(--line2);transition:background .12s,box-shadow .12s}
.hudmeter i.on{background:var(--text);box-shadow:0 0 6px rgba(255,255,255,.5)}
html[data-theme=light] .hudmeter i.on{box-shadow:none}
.hudeye{position:absolute;right:0;top:50%;transform:translateY(-50%);background:none;border:0;color:var(--muted);cursor:pointer;
  font:.6rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.14em;text-transform:uppercase;padding:.5em}
.hudeye:hover,.hudeye[aria-pressed=true]{color:var(--text)}
.hudcaps{min-height:1em;font:.66rem/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.16em;text-transform:uppercase;color:var(--warn)}
.hud.typing .reactor{filter:drop-shadow(0 0 30px rgba(255,255,255,.75))}
.reactor{transform:translate3d(calc(var(--px,0)*9px),calc(var(--py,0)*7px),0);transition:transform .25s ease-out,filter .2s}
.hudgrid{inset:-40px;transform:translate3d(calc(var(--px,0)*-16px),calc(var(--py,0)*-12px),0);transition:transform .3s ease-out}
@media (hover:none),(prefers-reduced-motion:reduce){.reactor,.hudgrid{transform:none}}

/* ---------- стили оформления: переключаются кнопкой 🎨, хранятся в браузере ---------- */
html[data-style=ruby]{
  --bg:#0a0607; --bg2:#100a0b; --panel:#140d0f; --panel2:#1b1114; --panel3:#25181c;
  --line:rgba(255,255,255,.08); --line2:rgba(255,120,130,.2);
  --text:#f6eeef; --muted:#a08c90; --faint:#6d5b5f;
  --accent:#e5384a; --accent2:#ff6b78; --accent-d:#a8121f; --on-accent:#fff;
  --accent-soft:rgba(229,56,74,.14); --accent-line:rgba(255,107,120,.45);
  --field:#0d0809; --code:#080506; --hover:rgba(255,120,130,.08); --radius:14px; --radius-s:10px}
html[data-style=ruby] body::before{background:
  radial-gradient(900px 520px at 12% -10%, rgba(229,56,74,.28), transparent 70%),
  radial-gradient(700px 520px at 105% 110%, rgba(229,56,74,.12), transparent 70%), var(--bg)}
html[data-style=ice]{
  --bg:#060a11; --bg2:#0a111b; --panel:#0d1522; --panel2:#121c2d; --panel3:#192740;
  --line:rgba(150,200,255,.09); --line2:rgba(120,190,255,.22);
  --text:#eaf3ff; --muted:#8aa0ba; --faint:#566a84;
  --accent:#5cc8ff; --accent2:#9ae0ff; --accent-d:#2a9ed6; --on-accent:#031420;
  --accent-soft:rgba(92,200,255,.13); --accent-line:rgba(92,200,255,.45);
  --field:#08101a; --code:#050a12; --hover:rgba(120,190,255,.08); --radius:18px; --radius-s:12px}
html[data-style=ice] body::before{background:
  radial-gradient(900px 560px at 50% -16%, rgba(92,200,255,.22), transparent 70%),
  radial-gradient(700px 520px at 100% 110%, rgba(110,120,255,.14), transparent 70%), var(--bg)}
html[data-style=neon]{
  --bg:#050605; --bg2:#080a08; --panel:#0a0d0a; --panel2:#0f130f; --panel3:#161c16;
  --line:rgba(170,255,70,.12); --line2:rgba(170,255,70,.28);
  --text:#ecffe0; --muted:#8fa383; --faint:#5b6b52;
  --accent:#b6ff3b; --accent2:#d4ff80; --accent-d:#8bd11f; --on-accent:#0a1200;
  --accent-soft:rgba(182,255,59,.1); --accent-line:rgba(182,255,59,.5);
  --field:#060806; --code:#040504; --hover:rgba(182,255,59,.07); --radius:4px; --radius-s:3px}
html[data-style=neon] body{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:14px}
html[data-style=neon] h1,html[data-style=neon] h2,html[data-style=neon] .section-title{text-transform:uppercase;letter-spacing:.08em}
html[data-style=neon] body::before{background:
  radial-gradient(800px 480px at 50% -14%, rgba(182,255,59,.16), transparent 70%), var(--bg)}
html[data-style=paper]{
  --bg:#f3eee4; --bg2:#f8f4ec; --panel:#fffdf8; --panel2:#f6f0e4; --panel3:#ece4d3;
  --line:rgba(40,30,10,.12); --line2:rgba(40,30,10,.26);
  --text:#1f1a12; --muted:#6f6552; --faint:#a29780;
  --accent:#1f1a12; --accent2:#3a3225; --accent-d:#0f0c07; --on-accent:#fbf7ee;
  --accent-soft:rgba(31,26,18,.07); --accent-line:rgba(31,26,18,.45);
  --field:#fffdf8; --code:#1f1a12; --hover:rgba(31,26,18,.05); --radius:10px; --radius-s:7px; color-scheme:light}
html[data-style=paper] body{font-family:"Georgia","Iowan Old Style","Times New Roman",serif}
html[data-style=paper] h1,html[data-style=paper] h2,html[data-style=paper] h3{font-weight:700;letter-spacing:-.01em}
html[data-style=paper] body::before{background:linear-gradient(180deg,var(--bg2),var(--bg))}
html[data-style=paper] body::after{opacity:0}
html[data-style=paper] .side{background:#fbf7ee}
html[data-style=paper] .mbar{background:rgba(251,247,238,.96)}
html[data-style=paper] .btn.primary{color:var(--on-accent)}
/* светлая тема внутри цветных стилей: берём их акцент, остальное — обычная светлая палитра */
html[data-style=ruby][data-theme=light]{--accent:#c4202e;--accent2:#e5434f;--on-accent:#fff;--accent-soft:rgba(196,32,46,.09);--accent-line:rgba(196,32,46,.4)}
html[data-style=ice][data-theme=light]{--accent:#0b78b8;--accent2:#2596d8;--on-accent:#fff;--accent-soft:rgba(11,120,184,.09);--accent-line:rgba(11,120,184,.4)}
html[data-style=neon][data-theme=light]{--accent:#3d7a00;--accent2:#5aa00a;--on-accent:#fff;--accent-soft:rgba(61,122,0,.09);--accent-line:rgba(61,122,0,.4)}
html[data-style=ruby][data-theme=light] body::before,html[data-style=ice][data-theme=light] body::before,html[data-style=neon][data-theme=light] body::before{background:linear-gradient(180deg,var(--bg2),var(--bg))}
.stylegrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:.7rem;margin:.9rem 0}
.stylecard{display:flex;flex-direction:column;gap:.5rem;padding:.6rem;border:2px solid var(--line);border-radius:12px;background:var(--panel);color:var(--text);cursor:pointer;text-align:left;font:inherit}
.stylecard:hover{border-color:var(--line2)}
.stylecard.on{border-color:var(--accent)}
.stylecard i{display:block;height:54px;border-radius:8px;border:1px solid rgba(128,128,128,.3)}
.stylecard b{font-size:.88rem}
.sw1{background:linear-gradient(135deg,#000 55%,#fff 55%)}
.sw2{background:linear-gradient(135deg,#140d0f 55%,#e5384a 55%)}
.sw3{background:linear-gradient(135deg,#0d1522 55%,#5cc8ff 55%)}
.sw4{background:linear-gradient(135deg,#0a0d0a 55%,#b6ff3b 55%)}
.sw5{background:linear-gradient(135deg,#f3eee4 55%,#1f1a12 55%)}

/* сообщения: списки, редактор, вкладки */
.seg{display:inline-flex;gap:.2rem;padding:.25rem;background:var(--panel);border:1px solid var(--line);border-radius:999px;margin-bottom:1rem}
.seg button{border:0;background:none;color:var(--muted);padding:.55em 1.3em;border-radius:999px;cursor:pointer;font:inherit;font-weight:560}
.seg button:hover{color:var(--text)}
.seg button.on{background:var(--accent);color:var(--on-accent)}
.sendgrid{display:grid;grid-template-columns:minmax(260px,340px) 1fr;gap:1rem;align-items:start}
.sendgrid>*{min-width:0}
.pickcard{position:sticky;top:1rem}
.pickcard h2,.compose-card h2{margin:0}
.compose-card{display:flex;flex-direction:column;gap:1.1rem;padding:1.2rem}
.compose-card .pickline.target{margin-bottom:0}
.picks.tall{max-height:min(62vh,560px)}
.pickline.target{padding:.7rem .9rem;margin-bottom:1rem;background:var(--accent-soft);border:1px solid var(--accent-line);border-radius:var(--radius-s)}
.compose{display:flex;flex-direction:column;gap:1.1rem}
.cfield{display:flex;flex-direction:column;gap:.4rem}
.cfield>label,.cfield .row>label{font-size:.74rem;font-weight:650;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.cfield>small{color:var(--faint);font-size:.76rem}
.compose textarea.msg{min-height:200px;font-size:1rem;line-height:1.5}
.swatches{display:flex;gap:.45rem;align-items:center;flex-wrap:wrap}
.sw{width:26px;height:26px;border-radius:50%;border:2px solid var(--line2);cursor:pointer;padding:0}
.sw:hover{transform:scale(1.12)}
.hash{color:var(--muted);margin-left:.5rem}
.hexin{max-width:110px}
.seg.small{margin:0;align-self:flex-start}
.seg.small button{padding:.4em 1em;font-size:.85rem}
.framebox{display:flex;flex-direction:column;gap:1rem;padding:1rem;border:1px dashed var(--line2);border-radius:var(--radius-s)}
.spbox{display:flex;flex-direction:column;gap:.5rem}
.avlist{display:flex;flex-direction:column;gap:.2rem;max-height:260px;overflow:auto;padding:.3rem;border:1px solid var(--line);border-radius:var(--radius-s);background:var(--field)}
.avrow{display:flex;align-items:center;gap:.7rem;padding:.4rem .5rem;border:1px solid transparent;border-radius:9px;background:none;color:var(--text);cursor:pointer;text-align:left;font:inherit}
.avrow:hover{background:var(--hover)}
.avrow.on{background:var(--accent-soft);border-color:var(--accent-line)}
.avrow img,.avph{width:36px;height:36px;border-radius:50%;object-fit:cover;flex:none}
.avph{display:grid;place-items:center;background:var(--panel3);font-weight:700}
.avrow b{display:block;font-size:.9rem}.avrow small{color:var(--muted);font-size:.75rem}
.dcava{overflow:hidden}.dcava img{width:100%;height:100%;object-fit:cover}
.spbox small{color:var(--faint)}
.tick{gap:.6rem;align-items:center}
.btn.big{padding:.85em 1.5em;font-size:1rem}
.dctext{white-space:pre-wrap;word-break:break-word;color:#dbdee1}
.dcping{display:inline-block;margin-top:.3rem;color:#c9cdfb;background:rgba(88,101,242,.3);border-radius:4px;padding:0 .3em}
@media (max-width:900px){.sendgrid{grid-template-columns:1fr}.pickcard{position:static}}

/* ======================================================================
   Стиль «Дом» — по умолчанию. Семейная бухгалтерия: винный фон, латунь,
   тонкая полоска как на костюме, крупные цифры с засечками.
   ====================================================================== */
html[data-style=house]{
  --bg:#13090f; --bg2:#1a0c14; --panel:#1f111a; --panel2:#291722; --panel3:#35202c;
  --line:rgba(226,196,130,.11); --line2:rgba(226,196,130,.26);
  --text:#f3ead9; --muted:#b4a08f; --faint:#7f6d66;
  --accent:#c9a45c; --accent2:#e3c481; --accent-d:#9a7a3b; --on-accent:#1b0e13;
  --accent-soft:rgba(201,164,92,.12); --accent-line:rgba(201,164,92,.5);
  --ok:#86c996; --warn:#e3a84f; --err:#f0736f;
  --field:#12080d; --code:#0d0509; --hover:rgba(226,196,130,.07);
  --radius:8px; --radius-s:5px; --glow:none; --edge:none; color-scheme:dark;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
}
html[data-style=house] body::before{background:
  radial-gradient(900px 480px at 8% -8%, rgba(120,28,52,.42), transparent 70%),
  radial-gradient(700px 520px at 104% 108%, rgba(201,164,92,.09), transparent 70%), var(--bg)}
/* полоска как на костюме: тонкие вертикальные линии, гаснут к низу */
html[data-style=house] body::after{opacity:1;background-image:repeating-linear-gradient(90deg,rgba(226,196,130,.05) 0 1px,transparent 1px 15px);
  background-size:auto;-webkit-mask-image:linear-gradient(180deg,#000,transparent 72%);mask-image:linear-gradient(180deg,#000,transparent 72%)}
html[data-style=house] h1,html[data-style=house] h2,html[data-style=house] h3{font-family:var(--serif);font-weight:600;letter-spacing:-.005em}
html[data-style=house] .head h1{font-size:2.15rem;line-height:1.1;letter-spacing:-.015em}
html[data-style=house] .head h1::after{content:"";display:block;width:46px;height:2px;margin-top:.7rem;background:var(--accent)}
html[data-style=house] .head p{font-size:.97rem;max-width:58ch}

/* боковая колонка — «корешок» книги */
html[data-style=house] .side{background:#10070c;border-right:1px solid var(--line2);box-shadow:inset -1px 0 0 rgba(0,0,0,.5)}
html[data-style=house] .brand b{font-family:var(--serif);font-size:1.08rem;font-weight:600}
html[data-style=house] .brand span{text-transform:none;letter-spacing:.02em;font-size:.76rem;font-style:italic}
html[data-style=house] .logo{background:radial-gradient(circle at 35% 30%,var(--accent2),var(--accent) 55%,var(--accent-d));color:var(--on-accent);
  border-radius:50%;font-family:var(--serif);font-weight:700;box-shadow:0 0 0 2px #10070c,0 0 0 3px var(--accent-d),0 6px 16px rgba(0,0,0,.5)}
html[data-style=house] .navgroup{text-transform:none;letter-spacing:.02em;font-family:var(--serif);font-style:italic;font-size:.85rem;font-weight:500;color:var(--muted)}
html[data-style=house] .nav button{border-radius:var(--radius-s);color:var(--muted)}
html[data-style=house] .nav button .ico{background:none;border:0;border-radius:0;color:var(--faint)}
html[data-style=house] .nav button:hover{color:var(--text);background:var(--hover)}
html[data-style=house] .nav button:hover .ico{color:var(--accent2)}
html[data-style=house] .nav button.active{color:var(--accent2);background:var(--accent-soft)}
html[data-style=house] .nav button.active .ico{color:var(--accent2);background:none;box-shadow:none}
html[data-style=house] .nav button.active::before{left:0;top:0;bottom:0;width:3px;border-radius:0;background:var(--accent);box-shadow:none}
html[data-style=house] .searchbtn{border-radius:var(--radius-s)}
html[data-style=house] .status{border-radius:var(--radius-s)}

/* карточки: строгие, как листы учётной книги; цифры — главный акцент страницы */
html[data-style=house] .card{background:var(--panel);border-radius:var(--radius);border:1px solid var(--line);box-shadow:none}
html[data-style=house] .card:hover{border-color:var(--line2)}
html[data-style=house] .stat{border-radius:var(--radius-s);padding:1.1rem 1.2rem 1rem;border-top:2px solid var(--accent-d)}
html[data-style=house] .stat::before,html[data-style=house] .stat::after{display:none}
html[data-style=house] .stat:hover{transform:none}
html[data-style=house] .stat small{font-size:.8rem;color:var(--muted);letter-spacing:0}
html[data-style=house] .stat b{font-family:var(--serif);font-size:2.2rem;font-weight:500;letter-spacing:-.02em;color:var(--accent2);line-height:1.05}
html[data-style=house] .stat.ok{border-top-color:var(--ok)} html[data-style=house] .stat.warn{border-top-color:var(--warn)}
html[data-style=house] .stat.ok b{color:var(--ok)} html[data-style=house] .stat.warn b{color:var(--warn)}
html[data-style=house] .section-title{text-transform:none;letter-spacing:0;font-family:var(--serif);font-style:italic;font-size:1.12rem;font-weight:500;color:var(--text)}
html[data-style=house] .section-title::after{background:linear-gradient(90deg,var(--line2),transparent)}
html[data-style=house] .cfield>label,html[data-style=house] .cfield .row>label{text-transform:none;letter-spacing:0;font-size:.86rem;font-weight:600;color:var(--text)}

/* кнопки и поля */
html[data-style=house] .btn{border-radius:var(--radius-s);background:transparent;border:1px solid var(--line2);color:var(--text)}
html[data-style=house] .btn:hover{background:var(--hover);border-color:var(--accent-line)}
html[data-style=house] .btn.primary,html[data-style=house] .btn.primary:hover{background:var(--accent);color:var(--on-accent);border-color:var(--accent);box-shadow:none;font-weight:650}
html[data-style=house] .btn.primary:hover{background:var(--accent2)}
html[data-style=house] .btn.danger{border-color:rgba(240,115,111,.5);color:var(--err)}
html[data-style=house] input,html[data-style=house] select,html[data-style=house] textarea{border-radius:var(--radius-s);background:var(--field)}
html[data-style=house] input:focus,html[data-style=house] select:focus,html[data-style=house] textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
html[data-style=house] input[type=radio]{border-radius:50%}
html[data-style=house] input[type=checkbox]{border-radius:4px}
html[data-style=house] .chip{border-radius:3px}
html[data-style=house] .seg{border-radius:var(--radius-s)}
html[data-style=house] .seg button{border-radius:3px}
html[data-style=house] .seg button.on{background:var(--accent);color:var(--on-accent)}
html[data-style=house] .pickline.target{border-radius:var(--radius-s)}
html[data-style=house] .picks label:has(input:checked){background:var(--accent-soft);border-color:var(--accent-line)}
html[data-style=house] input[type=checkbox]:checked,html[data-style=house] input[type=radio]:checked{background:var(--accent)}
html[data-style=house] input[type=checkbox]:checked::after{border-color:var(--on-accent)}
html[data-style=house] input[type=radio]:checked::after{background:var(--on-accent)}
html[data-style=house] .pulsecap b,html[data-style=house] .pulsehead h2{font-family:var(--serif)}
html[data-style=house] .note-c{border-radius:var(--radius-s)}
html[data-style=house] .dcprev{border-radius:var(--radius-s)}
html[data-style=house] ::selection{background:var(--accent);color:var(--on-accent)}

/* вход: тот же реактор, но латунный */
html[data-style=house] .hud{background:rgba(16,7,12,.72);border-color:var(--line2)}
html[data-style=house] .hud .c{border-color:var(--accent)}
html[data-style=house] .hud h1{font-family:var(--serif);letter-spacing:.14em}
html[data-style=house] .reactor{color:var(--accent2);filter:drop-shadow(0 0 16px rgba(201,164,92,.4))}
html[data-style=house] .hud.typing .reactor{filter:drop-shadow(0 0 30px rgba(227,196,129,.75))}
html[data-style=house] .hudmeter i.on{background:var(--accent);box-shadow:0 0 6px rgba(201,164,92,.6)}
html[data-style=house] .hudbtn{border-color:var(--accent);color:var(--accent2)}
html[data-style=house] .hudbtn:hover:not(:disabled){background:var(--accent);color:var(--on-accent)}
html[data-style=house] .hudwrap::before{background:radial-gradient(closest-side at 50% 40%,rgba(201,164,92,.16),transparent 72%)}
html[data-style=house] .hudwrap::after{background:linear-gradient(180deg,transparent,rgba(201,164,92,.06),transparent)}
html[data-style=house] .hudsay,html[data-style=house] .hudlog b{color:var(--accent2)}
html[data-style=house] .hudgrid{background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='48' height='48'%3E%3Cpath d='M24 20v8M20 24h8' stroke='%23c9a45c' stroke-opacity='.28' fill='none'/%3E%3C/svg%3E")}
html[data-style=house] .stylecard.on{border-color:var(--accent)}
.sw0{background:linear-gradient(135deg,#13090f 55%,#c9a45c 55%)}
@media (max-width:1000px){html[data-style=house] .side{background:#10070c}html[data-style=house] .mbar{background:rgba(16,7,12,.96)}}
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
  if (!res.ok) {
    const fallback = res.status === 403 ? 'Нет прав на это действие'
      : res.status >= 500 ? 'Внутренняя ошибка бота (' + res.status + ')' : 'Ошибка ' + res.status;
    const er = new Error((data && data.error) || fallback);
    er.status = res.status;
    er.detail = data && data.detail;
    throw er;
  }
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
/* Экран входа в стиле HUD: реактор, бегущая проверка систем, телеметрия по углам,
   ответ «доступ разрешён / запрещён». Всё без внешних файлов и без лишней нагрузки. */
const REACTOR_SVG = '<svg viewBox="0 0 220 220" fill="none" stroke="currentColor" focusable="false">'
  + '<defs><radialGradient id="rcore"><stop offset="0" stop-color="currentColor" stop-opacity="1"/>'
  + '<stop offset=".55" stop-color="currentColor" stop-opacity=".85"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></radialGradient></defs>'
  + '<g class="rg r1"><circle cx="110" cy="110" r="106" stroke-width="1" stroke-dasharray="1 6" opacity=".6"/>'
  + '<circle cx="110" cy="110" r="98" stroke-width="8" stroke-dasharray="2 8.65" opacity=".9"/>'
  + '<circle cx="110" cy="110" r="90" stroke-width=".8" opacity=".35"/></g>'
  + '<g class="rg r2"><circle cx="110" cy="110" r="78" stroke-width="2.4" stroke-dasharray="86 20 120 20 96 20 100 20"/>'
  + '<circle cx="110" cy="110" r="71" stroke-width=".8" opacity=".45"/></g>'
  + '<g class="rg r3"><path d="M110 62 L152 134 L68 134 Z" stroke-width="1.2" opacity=".7"/>'
  + '<circle cx="110" cy="110" r="54" stroke-width="1" stroke-dasharray="1 5" opacity=".7"/></g>'
  + '<g class="rg r4"><circle cx="110" cy="110" r="42" stroke-width="2" stroke-dasharray="14 6"/></g>'
  + '<circle cx="110" cy="110" r="32" stroke-width="10" stroke-dasharray="7.2 8.1" opacity=".9"/>'
  + '<circle class="core" cx="110" cy="110" r="22" fill="url(#rcore)" stroke="none"/>'
  + '<circle cx="110" cy="110" r="9" fill="currentColor" stroke="none" class="core2"/></svg>';

function clockText() {
  const d = new Date(), p = (n) => String(n).padStart(2, '0');
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}
function dateText() {
  const d = new Date(), p = (n) => String(n).padStart(2, '0');
  return p(d.getDate()) + '.' + p(d.getMonth() + 1) + '.' + d.getFullYear();
}

function hudGreeting() {
  const hr = new Date().getHours();
  return (hr < 5 ? 'Доброй ночи' : hr < 12 ? 'Доброе утро' : hr < 18 ? 'Добрый день' : 'Добрый вечер') + '. Жду код доступа';
}

function showLogin(msg) {
  stopTimer();
  state.isDirty = () => false;
  const input = h('input', { type: 'password', placeholder: '••••••••', autocomplete: 'current-password', 'aria-label': 'Пароль', spellcheck: 'false' });
  const err = h('div', { class: 'err hud-err', role: 'alert' }, msg || '');
  const stat = h('div', { class: 'hudstat' }, msg ? 'Сессия завершена' : 'Ожидание идентификации');
  const clock = h('span', { id: 'hudclock' }, clockText());
  const reactor = h('div', { class: 'reactor', 'aria-hidden': 'true' });
  reactor.innerHTML = REACTOR_SVG;
  const btn = h('button', { class: 'hudbtn', type: 'submit' }, h('span', null, 'Авторизация'));
  const say = h('div', { class: 'hudsay', 'aria-live': 'polite' }, '');
  const METER = 14;
  const meter = h('div', { class: 'hudmeter', 'aria-hidden': 'true' }, Array.from({ length: METER }, () => h('i')));
  const caps = h('div', { class: 'hudcaps', role: 'status' }, '');
  const eye = h('button', { class: 'hudeye', type: 'button', 'aria-pressed': 'false', 'aria-label': 'Показать пароль', tabindex: '-1',
    onclick: () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      eye.setAttribute('aria-pressed', show ? 'true' : 'false');
      eye.textContent = show ? 'скрыть' : 'показать';
      input.focus();
    } }, 'показать');
  const checks = [['Ядро питания', 'норма'], ['Канал шифрования', 'активен'], ['Идентификация', 'ожидание']];
  const log = h('div', { class: 'hudlog', 'aria-hidden': 'true' }, checks.map(([t, v], i) =>
    h('div', { style: 'animation-delay:' + (0.45 + i * 0.4).toFixed(2) + 's' }, h('span', null, t), h('i'), h('b', null, v))));
  const wrap = h('div', { class: 'hudwrap' });
  const form = h('form', {
    class: 'hud',
    autocomplete: 'on',
    onsubmit: async (e) => {
      e.preventDefault();
      if (btn.disabled) return;
      err.textContent = '';
      form.classList.remove('deny');
      form.classList.add('checking');
      btn.disabled = true;
      stat.textContent = 'Проверка доступа';
      try {
        await api('/api/login', { method: 'POST', body: { password: input.value } });
        form.classList.remove('checking');
        form.classList.add('granted');
        wrap.classList.add('granted');
        stat.textContent = 'Доступ разрешён';
        await sleep(950);
        await boot();
      } catch (ex) {
        form.classList.remove('checking');
        btn.disabled = false;
        void form.offsetWidth;
        form.classList.add('deny');
        stat.textContent = 'Доступ запрещён';
        err.textContent = ex.message;
        input.select();
      }
    }
  },
    h('i', { class: 'c tl' }), h('i', { class: 'c tr' }), h('i', { class: 'c bl' }), h('i', { class: 'c br' }),
    reactor,
    h('div', { class: 'hudcap' }, 'Защищённый доступ'),
    h('h1', null, 'Панель управления'),
    say,
    log,
    h('label', { class: 'hudfield' }, input, eye),
    meter,
    caps,
    btn,
    stat,
    err,
    h('div', { class: 'hudfoot' }, h('span', null, 'Система в сети'), clock));
  wrap.append(
    h('div', { class: 'hudgrid' }),
    h('div', { class: 'tele tl' }, 'Узел доступа'),
    h('div', { class: 'tele tr' }, dateText()),
    h('div', { class: 'tele bl' }, 'Шифрование AES-256'),
    h('div', { class: 'tele br' }, 'Соединение защищено'),
    form,
    h('div', { class: 'hudflash' }));
  $app.replaceChildren(wrap);
  state.timer = setInterval(() => { const c = document.getElementById('hudclock'); if (c) c.textContent = clockText(); }, 1000);

  // приветствие печатается по буквам; если экран уже заменили, печать сама останавливается
  const phrase = hudGreeting();
  let ch = 0;
  const typer = setInterval(() => {
    if (!say.isConnected || ch >= phrase.length) { clearInterval(typer); if (say.isConnected) say.textContent = phrase; return; }
    ch += 1; say.textContent = phrase.slice(0, ch);
  }, 38);

  // шкала «заряда» растёт с каждым символом, реактор вспыхивает при вводе
  let typingT = 0;
  input.addEventListener('input', () => {
    const lit = Math.min(METER, input.value.length);
    meter.childNodes.forEach((n, i) => n.classList.toggle('on', i < lit));
    form.classList.add('typing');
    clearTimeout(typingT);
    typingT = setTimeout(() => form.classList.remove('typing'), 450);
  });
  const capsCheck = (e) => {
    const on = !!(e.getModifierState && e.getModifierState('CapsLock'));
    caps.textContent = on ? 'Включён Caps Lock' : '';
  };
  input.addEventListener('keydown', capsCheck);
  input.addEventListener('keyup', capsCheck);
  input.addEventListener('blur', () => { caps.textContent = ''; });

  // лёгкий параллакс: реактор и сетка чуть следуют за курсором
  let raf = 0;
  wrap.addEventListener('pointermove', (e) => {
    if (raf || e.pointerType === 'touch') return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      wrap.style.setProperty('--px', ((e.clientX / window.innerWidth) * 2 - 1).toFixed(3));
      wrap.style.setProperty('--py', ((e.clientY / window.innerHeight) * 2 - 1).toFixed(3));
    });
  });
  input.focus();
}

/* ---------- тема и избранное ---------- */
/* Тема и закреплённые экраны живут в localStorage браузера: сервер их не хранит,
   поэтому каждый заходит со своими настройками и ничего не ломает другому. */
function applyTheme() {
  let t = '';
  try { t = localStorage.getItem('hz_theme') || ''; } catch (e) { /* приватный режим */ }
  const st = getStyle();
  document.documentElement.dataset.style = st;
  document.documentElement.dataset.theme = st === 'paper' ? 'light' : st === 'house' ? 'dark' : (t === 'light' ? 'light' : 'dark');
}
const STYLES = [['house', 'Дом', 'sw0'], ['mono', 'Моно', 'sw1'], ['ruby', 'Алый', 'sw2'], ['ice', 'Лёд', 'sw3'], ['neon', 'Неон', 'sw4'], ['paper', 'Бумага', 'sw5']];
function getStyle() {
  let v = '';
  try { v = localStorage.getItem('hz_style') || ''; } catch (e) { /* приватный режим */ }
  return STYLES.some(([k]) => k === v) ? v : 'house';
}
function openStyles() {
  const dlg = document.getElementById('dlg');
  const draw = () => {
    const cur = getStyle();
    dlg.replaceChildren(h('h3', null, 'Стиль оформления'),
      h('p', { class: 'muted' }, 'Меняется сразу и запоминается в этом браузере. «Дом» всегда тёмный, «Бумага» всегда светлая.'),
      h('div', { class: 'stylegrid' }, STYLES.map(([k, label, cls]) => h('button', {
        class: 'stylecard' + (k === cur ? ' on' : ''), onclick: () => {
          try { localStorage.setItem('hz_style', k); } catch (e) { /* приватный режим */ }
          applyTheme(); draw();
          document.querySelectorAll('.footbtns .btn.icon').forEach((b) => { if (b.dataset.role === 'theme') b.replaceChildren(themeIcon()); });
        } }, h('i', { class: cls }), h('b', null, label)))),
      h('div', { class: 'row end' }, h('button', { class: 'btn primary', onclick: () => dlg.close() }, 'Готово')));
  };
  draw();
  dlg.showModal();
}
function themeIcon() { return document.documentElement.dataset.theme === 'light' ? '☀️' : '🌙'; }
function toggleTheme() {
  if (getStyle() === 'paper' || getStyle() === 'house') { toast('Этот стиль не меняет тему: он всегда ' + (getStyle() === 'paper' ? 'светлый' : 'тёмный') + '. Другой стиль — кнопка 🎨.'); return; }
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  try { localStorage.setItem('hz_theme', next); } catch (e) { /* приватный режим */ }
  applyTheme();
  document.querySelectorAll('.footbtns .btn.icon[data-role=theme]').forEach((b) => { b.replaceChildren(themeIcon()); });
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
  ['plan', 'Планировщик', 'clock', 'Семья'],
  ['notes', 'Заметки', 'note', 'Семья'],
  ['screens', 'Экраны', 'screen', 'Семья'],
  ['texts', 'Тексты', 'type', 'Семья'],
  ['stickers', 'Стикеры', 'image', 'Семья'],
  ['kazna', 'Казна', 'bank', 'Семья'],
  ['emojis', 'Эмодзи', 'smile', 'Семья'],
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
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
  bank: '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 12h.01M18 12h.01"/>',
  note: '<path d="M15.5 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8.5L15.5 3Z"/><path d="M15 3v6h6"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="13" y2="17"/>',
  clock: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
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

function openPalette() {
  if (!document.getElementById('shell')) return;
  const old = document.getElementById('pal');
  if (old) old.remove();
  const dlg = h('dialog', { id: 'pal', class: 'pal' });
  const input = h('input', { type: 'search', placeholder: 'Куда перейти или что сделать…', 'aria-label': 'Поиск по разделам' });
  const list = h('div', { class: 'palist', role: 'listbox' });
  const items = PAGES.map(([id, label, ico, g]) => ({ label, hint: g, ico, run: () => go(id) })).concat([
    { label: 'Обновить страницу', hint: 'Действие', ico: 'pulse', run: () => go(state.page) },
    { label: 'Сменить тему', hint: 'Действие', ico: 'screen', run: toggleTheme },
    { label: 'Стиль оформления', hint: 'Действие', ico: 'screen', run: openStyles },
    { label: 'Выйти', hint: 'Действие', ico: 'out', run: logout },
  ]);
  let shown = [], sel = 0;
  function pick(i) { const it = shown[i]; if (!it) return; dlg.close(); it.run(); }
  function draw() {
    const q = input.value.trim().toLowerCase();
    shown = items.filter((it) => !q || it.label.toLowerCase().includes(q));
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    list.replaceChildren(...(shown.length ? shown.map((it, i) =>
      h('button', { class: 'paitem' + (i === sel ? ' on' : ''), type: 'button', onclick: () => pick(i) },
        icon(it.ico), h('span', { class: 'grow' }, it.label), h('small', null, it.hint)))
      : [h('div', { class: 'none' }, 'Ничего не найдено')]));
    const on = list.querySelector('.on');
    if (on) on.scrollIntoView({ block: 'nearest' });
  }
  input.addEventListener('input', () => { sel = 0; draw(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(shown.length - 1, sel + 1); draw(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); draw(); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(sel); }
  });
  dlg.addEventListener('close', () => dlg.remove());
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  dlg.append(input, list);
  document.body.append(dlg);
  draw();
  dlg.showModal();
  input.focus();
}

function bindHotkeys() {
  if (window.__hzKeys) return;
  window.__hzKeys = true;
  window.addEventListener('keydown', (e) => {
    const t = (e.target && e.target.tagName) || '';
    const typing = t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT';
    if ((e.ctrlKey || e.metaKey) && (e.code === 'KeyK' || String(e.key).toLowerCase() === 'k')) { e.preventDefault(); openPalette(); }
    else if (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); openPalette(); }
  });
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
    h('button', { class: 'searchbtn', type: 'button', onclick: openPalette }, icon('search'), h('span', null, 'Поиск'), h('kbd', null, 'Ctrl K')),
    nav,
    h('div', { class: 'sidefoot' },
      h('div', { class: 'status' }, h('span', { class: 'dot', id: 'live', title: 'Бот на связи' }),
        h('span', { id: 'livetxt' }, 'Бот ', h('b', null, 'на связи'))),
      h('div', { class: 'footbtns' },
        h('button', { class: 'btn icon', title: 'Светлая или тёмная тема', 'data-role': 'theme', onclick: toggleTheme }, themeIcon()),
        h('button', { class: 'btn icon', title: 'Стиль оформления', 'aria-label': 'Стиль оформления', onclick: openStyles }, '🎨'),
        h('button', { class: 'btn', onclick: logout }, icon('out'), 'Выйти'))));
  const mbar = h('header', { class: 'mbar' },
    h('button', { class: 'btn icon', 'aria-label': 'Открыть меню', onclick: () => setMenu(true) }, icon('menu')),
    h('div', { class: 'logo' }, (state.botName || 'H').slice(0, 1).toUpperCase()),
    h('b', null, state.botName),
    h('div', { class: 'spacer' }),
    h('button', { class: 'btn icon', 'aria-label': 'Поиск по разделам', onclick: openPalette }, icon('search')));
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
    const view = await ({ overview: pageOverview, apps: pageApps, send: pageSend, plan: pagePlan, notes: pageNotes, screens: pageScreens,
      texts: pageTexts, stickers: pageStickers, kazna: pageKazna, emojis: pageEmojis,
      diag: pageDiag, audit: pageAudit, shots: pageShots,
      modules: pageModules, settings: pageSettings, logs: pageLogs, control: pageControl })[page]();
    if (state.page === page) content.replaceChildren(view);
    markLive(true);
  } catch (e) {
    markLive(false);
    if (state.page === page) content.replaceChildren(errorCard(e, () => go(page)));
  }
}

function friendlyError(e) {
  const st = e && e.status, m = (e && e.message) || '';
  if (st === 401) return 'Сессия закончилась. Войдите снова.';
  if (st === 403) return 'Это действие запрещено. Если это про Discord, у бота не хватает прав в канале или на сервере.';
  if (st === 404) return 'Такого раздела или записи уже нет.';
  if (/не загрузился|не импортируется/.test(m)) return 'Раздел не смог загрузиться при старте бота. Причина записана в логах.';
  if (st === 503) return 'Раздел сейчас недоступен.';
  if (st >= 500) return 'Внутри бота что-то сломалось. Это не ваша ошибка: полный текст лежит в разделе «Логи».';
  if (/Нет связи/.test(m)) return 'Панель не отвечает. Возможно, бот перезапускается: подождите полминуты.';
  return 'Не получилось загрузить данные.';
}

function errorCard(e, retry) {
  return h('div', { class: 'card errcard' },
    h('h2', null, 'Не получилось открыть раздел'),
    h('p', { class: 'muted' }, friendlyError(e)),
    h('pre', { class: 'errdetail mono' }, (e && e.message) || String(e)),
    e && e.detail ? h('p', { class: 'muted small' }, e.detail) : null,
    h('div', { class: 'row' },
      h('button', { class: 'btn primary', onclick: retry }, 'Повторить'),
      h('button', { class: 'btn', onclick: () => go('logs') }, 'Открыть логи')));
}

/* «Обновить в Discord» — в шапке каждого экрана. Сохранённая с сайта надпись сразу попадает в
   новые сообщения, но карточки, которые бот уже выложил в каналы, Discord хранит у себя и сам
   их не переклеивает. Кнопка просит нужный раздел переложить свою витрину текущими текстами.
   Список того, что умеет, объявляют сами коги, поэтому в поле нет мёртвых пунктов. */
let RF_TARGETS = null;

async function rfLoad() {
  if (!RF_TARGETS) {
    const r = await api('/api/refresh').catch(() => ({ targets: [] }));
    RF_TARGETS = r.targets || [];
  }
  return RF_TARGETS;
}

function refreshCtl() {
  const sel = h('select', { 'aria-label': 'Что переложить в Discord' });
  const btn = h('button', { class: 'btn', onclick: run }, '🔄 Обновить в Discord');
  const box = h('div', { class: 'row rfctl' }, sel, btn);
  const sync = () => {
    const opt = sel.selectedOptions[0];
    btn.disabled = !sel.value || !!(opt && opt.disabled);
  };
  function fill(targets) {
    sel.replaceChildren(...targets.map((t) => h('option', {
      value: t.off ? '' : t.ref, disabled: t.off }, t.title + ' — ' + t.where)));
    box.style = targets.some((t) => !t.off) ? '' : 'display:none';
    sync();
  }
  async function run() {
    if (!sel.value) return;
    btn.disabled = true;
    const r = await api('/api/refresh', { method: 'POST', body: { ref: sel.value } })
      .catch((e) => ({ error: e.message }));
    if (r.error) toast(r.error, 'err');
    else toast(r.did || 'Готово', 'ok');
    RF_TARGETS = null;
    fill(await rfLoad());
  }
  sel.addEventListener('change', sync);
  rfLoad().then(fill);
  return box;
}

function pageHead(title, sub, ...actions) {
  return h('div', { class: 'head' }, h('div', null, h('h1', null, title), sub ? h('p', { class: 'muted' }, sub) : null), h('div', { class: 'row' }, refreshCtl(), actions));
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
  const refs = {};
  const stat = (label, value, kind, key) => {
    const b = h('b', null, value);
    const card = h('div', { class: 'card stat ' + (kind || '') }, h('small', null, label), b);
    if (key) refs[key] = { card, b };
    return card;
  };
  const put = (key, value, kind) => {
    const r = refs[key];
    if (r) { r.b.textContent = String(value); r.card.className = 'card stat ' + (kind || ''); }
  };
  const stats = h('div', { class: 'stats' },
    stat('Статус', d.ready ? 'В сети' : 'Запускается', d.ready ? 'ok' : 'warn', 'st'),
    stat('Пинг', d.latency_ms == null ? '—' : d.latency_ms + ' мс', '', 'ping'),
    stat('Работает', fmtUptime(d.uptime_s), '', 'up'),
    stat('Память', d.memory_mb == null ? '—' : d.memory_mb + ' МБ', '', 'mem'),
    stat('Серверов', d.guilds.length, '', 'srv'),
    stat('Модулей', d.modules_loaded + ' / ' + d.modules_total, '', 'mod')
  );
  const dbKeys = Object.keys(d.stats || {});
  const db = dbKeys.length ? [
    h('div', { class: 'section-title' }, 'База данных'),
    h('div', { class: 'stats' }, dbKeys.map((k) => stat(DB_LABELS[k] || k, d.stats[k], '', 'db_' + k)))
  ] : null;
  const guilds = h('div', { class: 'guilds' }, d.guilds.map((g) => {
    const okIcon = typeof g.icon === 'string' && g.icon.startsWith('https://cdn.discordapp.com/');
    return h('div', { class: 'card guild' },
      okIcon ? h('img', { src: g.icon, alt: '', referrerpolicy: 'no-referrer' }) : h('div', { class: 'ava' }, (g.name || '?').slice(0, 1).toUpperCase()),
      h('div', { style: null }, h('b', null, g.name), h('span', { class: 'muted small' }, (g.members == null ? '?' : g.members) + ' участников')));
  }));
  const pulse = pulseChart(d.latency_ms);
  state.timer = setInterval(async () => {
    try {
      const n = await api('/api/overview');
      pulse.push(n.latency_ms);
      put('st', n.ready ? 'В сети' : 'Запускается', n.ready ? 'ok' : 'warn');
      put('ping', n.latency_ms == null ? '—' : n.latency_ms + ' мс');
      put('up', fmtUptime(n.uptime_s));
      put('mem', n.memory_mb == null ? '—' : n.memory_mb + ' МБ');
      put('srv', n.guilds.length);
      put('mod', n.modules_loaded + ' / ' + n.modules_total);
      Object.keys(n.stats || {}).forEach((k) => put('db_' + k, n.stats[k]));
      markLive(true);
    } catch (e) { markLive(false); }
  }, 5000);
  return h('div', null,
    pageHead(d.bot_name || 'Бот', (d.user ? d.user + (d.user_id ? ' · ID ' + d.user_id : '') : 'Не авторизован в Discord') + ' · данные обновляются сами раз в 5 секунд',
      h('button', { class: 'btn', onclick: () => go('overview') }, 'Обновить')),
    stats, pulse, db,
    h('div', { class: 'section-title' }, 'Серверы'),
    d.guilds.length ? guilds : h('p', { class: 'muted' }, 'Бот пока ни на одном сервере.')
  );
}

/* ---------- Пульс бота (живой график на обзоре) ---------- */
function pulseChart(first) {
  const N = 40;
  const lat = [];
  const cv = h('canvas', { class: 'pulse', 'aria-label': 'График пинга бота за последние минуты', role: 'img' });
  const hi = h('b', null, '—'), lo = h('b', null, '—'), avg = h('b', null, '—'), now = h('b', null, '—');
  const legend = h('div', { class: 'pulsecap' },
    h('span', null, 'Сейчас ', now), h('span', null, 'Мин ', lo), h('span', null, 'Средний ', avg), h('span', null, 'Макс ', hi));
  const H = 150;
  function draw() {
    const w = cv.clientWidth || 640, dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(H * dpr); }
    const c = cv.getContext('2d');
    if (!c) return;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, w, H);
    const cs = getComputedStyle(document.documentElement);
    const fg = cs.getPropertyValue('--accent2').trim() || cs.getPropertyValue('--text').trim() || '#fff';
    const grid = cs.getPropertyValue('--line2').trim() || 'rgba(255,255,255,.2)';
    c.lineWidth = 1; c.strokeStyle = grid; c.globalAlpha = .5;
    for (let i = 1; i < 4; i++) { const y = Math.round(H * i / 4) + .5; c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke(); }
    for (let x = 0; x < w; x += 48) { c.beginPath(); c.moveTo(x + .5, 0); c.lineTo(x + .5, H); c.stroke(); }
    c.globalAlpha = 1;
    const pts = lat.filter((v) => v !== null);
    if (!pts.length) {
      c.fillStyle = grid; c.font = '12px ui-monospace,Menlo,monospace'; c.textAlign = 'center';
      c.fillText('жду первые замеры…', w / 2, H / 2); return;
    }
    const top = Math.max(120, Math.max(...pts) * 1.25);
    const step = w / (N - 1), off = 0;
    const xy = lat.map((v, i) => (v === null ? null : [(off + i) * step, H - 14 - (v / top) * (H - 30)]));
    c.lineJoin = 'round'; c.lineCap = 'round';
    const live = xy.filter(Boolean);
    const g = c.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, 'rgba(255,255,255,.16)'); g.addColorStop(1, 'rgba(255,255,255,0)');
    c.beginPath(); live.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])));
    c.lineTo(live[live.length - 1][0], H); c.lineTo(live[0][0], H); c.closePath();
    c.fillStyle = fg; c.globalAlpha = .1; c.fill(); c.globalAlpha = 1;
    c.beginPath(); live.forEach((p, i) => (i ? c.lineTo(p[0], p[1]) : c.moveTo(p[0], p[1])));
    c.strokeStyle = fg; c.lineWidth = 2; c.shadowColor = fg; c.shadowBlur = 8; c.stroke(); c.shadowBlur = 0;
    const last = live[live.length - 1];
    c.beginPath(); c.arc(last[0], last[1], 7, 0, 6.2832); c.globalAlpha = .18; c.fillStyle = fg; c.fill();
    c.globalAlpha = 1; c.beginPath(); c.arc(last[0], last[1], 3.2, 0, 6.2832); c.fill();
    c.fillStyle = grid; c.font = '10px ui-monospace,Menlo,monospace'; c.textAlign = 'left';
    c.fillText(Math.round(top) + ' мс', 6, 12);
  }
  function push(ms) {
    lat.push(typeof ms === 'number' ? ms : null);
    if (lat.length > N) lat.shift();
    const pts = lat.filter((v) => v !== null);
    now.textContent = pts.length ? pts[pts.length - 1] + ' мс' : '—';
    lo.textContent = pts.length ? Math.min(...pts) + ' мс' : '—';
    hi.textContent = pts.length ? Math.max(...pts) + ' мс' : '—';
    avg.textContent = pts.length ? Math.round(pts.reduce((a, b) => a + b, 0) / pts.length) + ' мс' : '—';
    draw();
  }
  push(first);
  if (typeof ResizeObserver === 'function') new ResizeObserver(draw).observe(cv);
  const box = h('div', { class: 'card pulsebox' },
    h('div', { class: 'pulsehead' }, h('h2', null, 'Пульс бота'), h('small', { class: 'muted' }, 'пинг до Discord, замер раз в 5 секунд, на графике последние ~3 минуты')), cv, legend);
  box.push = push;
  requestAnimationFrame(draw);
  return box;
}

/* ---------- Заметки штаба ---------- */
const NOTE_TONES = [['', 'Обычная'], ['white', 'Светлая'], ['ok', 'Зелёная'], ['warn', 'Жёлтая'], ['bad', 'Красная']];
function fmtWhen(ts) {
  return new Date(ts * 1000).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

async function pageNotes() {
  let notes = (await api('/api/notes')).notes || [];
  let tone = '', pinNew = false, q = '';
  const grid = h('div', { class: 'notes' });
  const count = h('small', { class: 'muted' }, '');
  const ta = h('textarea', { class: 'msg', maxlength: '2000', placeholder: 'Договорённость, список дел, мысль на потом…  Ctrl+Enter — добавить' });
  const tones = h('div', { class: 'tones', role: 'radiogroup', 'aria-label': 'Цвет заметки' });
  function drawTones() {
    tones.replaceChildren(...NOTE_TONES.map(([k, label]) => h('button', {
      type: 'button', class: 'tone t-' + (k || 'plain') + (k === tone ? ' on' : ''), title: label, 'aria-label': label,
      role: 'radio', 'aria-checked': k === tone ? 'true' : 'false', onclick: () => { tone = k; drawTones(); }
    })));
  }
  async function save(n, fields) {
    try {
      const r = await api('/api/notes/update', { method: 'POST', body: Object.assign({ id: n.id }, fields) });
      Object.assign(n, r.note);
      draw();
    } catch (e) { toast(e.message, 'err'); }
  }
  function card(n) {
    const body = h('div', { class: 'nbody' }, richNode(n.text));
    const el = h('article', { class: 'card note-c t-' + (n.color || 'plain') + (n.pinned ? ' pinned' : '') }, body);
    const edit = () => {
      const t = h('textarea', { class: 'msg', maxlength: '2000' });
      t.value = n.text;
      const row = h('div', { class: 'row end' },
        h('button', { class: 'btn', onclick: draw }, 'Отмена'),
        h('button', { class: 'btn primary', onclick: () => save(n, { text: t.value }) }, 'Сохранить'));
      body.replaceChildren(t, row);
      t.focus();
    };
    el.append(h('footer', null,
      h('small', { class: 'muted' }, fmtWhen(n.ts)),
      h('span', { class: 'nact' },
        h('button', { class: 'ibtn' + (n.pinned ? ' on' : ''), title: n.pinned ? 'Открепить' : 'Закрепить наверху', 'aria-label': 'Закрепить',
          onclick: () => save(n, { pinned: !n.pinned }) }, n.pinned ? '★' : '☆'),
        h('button', { class: 'ibtn', title: 'Сменить цвет', 'aria-label': 'Сменить цвет', onclick: () => {
          const i = NOTE_TONES.findIndex(([k]) => k === (n.color || ''));
          save(n, { color: NOTE_TONES[(i + 1) % NOTE_TONES.length][0] });
        } }, '◐'),
        h('button', { class: 'ibtn', title: 'Править', 'aria-label': 'Править', onclick: edit }, '✎'),
        h('button', { class: 'ibtn', title: 'Удалить', 'aria-label': 'Удалить', onclick: async () => {
          if (!(await confirmBox('Удалить заметку?', 'Она пропадёт у всех, кто пользуется панелью.', 'Удалить', true))) return;
          try { await api('/api/notes/delete', { method: 'POST', body: { id: n.id } }); notes = notes.filter((x) => x.id !== n.id); draw(); }
          catch (e) { toast(e.message, 'err'); }
        } }, '✕')
      )));
    return el;
  }
  function draw() {
    const s = q.trim().toLowerCase();
    const list = notes.filter((n) => !s || n.text.toLowerCase().includes(s))
      .sort((a, b) => (b.pinned - a.pinned) || (b.ts - a.ts));
    count.textContent = notes.length ? (s ? 'Найдено ' + list.length + ' из ' + notes.length : 'Всего заметок: ' + notes.length) : '';
    grid.replaceChildren(...list.map(card));
    if (!list.length) grid.append(h('div', { class: 'card empty' }, notes.length ? 'По запросу ничего нет.' : 'Пока пусто. Первая заметка — выше.'));
  }
  async function add() {
    if (!ta.value.trim()) { toast('Напиши хоть что-нибудь', 'err'); return; }
    try {
      const r = await api('/api/notes/add', { method: 'POST', body: { text: ta.value, color: tone, pinned: pinNew } });
      notes.push(r.note); ta.value = ''; draw();
    } catch (e) { toast(e.message, 'err'); }
  }
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); add(); } });
  const pin = h('input', { type: 'checkbox', onchange: (e) => { pinNew = e.target.checked; } });
  const search = h('input', { type: 'search', placeholder: 'Найти в заметках…', 'aria-label': 'Поиск по заметкам' });
  search.addEventListener('input', () => { q = search.value; draw(); });
  drawTones(); draw();
  return h('div', null,
    pageHead('Заметки штаба', 'Общий блокнот: то, что записано здесь, видят все, кто входит в панель.'),
    h('div', { class: 'card form composer' }, h('div', { class: 'row' }, emojiButton(ta)), ta,
      h('div', { class: 'row between' }, h('div', { class: 'row' }, tones, h('label', { class: 'row small' }, pin, 'Закрепить')),
        h('button', { class: 'btn primary', onclick: add }, 'Добавить'))),
    h('div', { class: 'row between notebar' }, search, count),
    grid);
}

/* ---------- Планировщик сообщений ---------- */
function toLocalInput(d) {
  const p = (n) => String(n).padStart(2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes());
}
function inText(sec) {
  if (sec < 60) return 'меньше минуты';
  const m = Math.floor(sec / 60);
  if (m < 60) return m + ' мин';
  const hh = Math.floor(m / 60);
  if (hh < 24) return hh + ' ч ' + (m % 60) + ' мин';
  return Math.floor(hh / 24) + ' д ' + (hh % 24) + ' ч';
}
const REPEAT_RU = { daily: 'каждый день', weekly: 'каждую неделю' };

async function pagePlan() {
  const cq = h('input', { type: 'search', placeholder: 'Имя канала…' });
  const chBox = h('div', { class: 'picks' });
  const chLine = h('div', { class: 'pickline' });
  let channelId = '', chanRows = [];
  function sayChannel() {
    const c = chanRows.find((x) => x.id === channelId);
    chLine.replaceChildren(h('span', null, c ? 'Отправим в ' : 'Канал не выбран — нажми на строчку.'));
    if (c) chLine.append(h('b', null, chanLabel(c)));
    if (c && c.can_write === false) chLine.append(chip('нет прав писать', 'err'));
  }
  async function loadChannels() {
    const d = await api('/api/channels?q=' + encodeURIComponent(cq.value.trim()));
    chanRows = d.rows;
    chBox.replaceChildren(...d.rows.map((c) => h('label', null,
      h('input', { type: 'radio', name: 'pchan', value: c.id, checked: c.id === channelId, onchange: () => { channelId = c.id; sayChannel(); } }),
      h('span', { class: 'grow' }, h('b', null, chanLabel(c)), h('small', null, chanSub(c))),
      c.can_write === false ? chip('нет прав', 'err') : null)));
    if (!d.rows.length) chBox.append(h('div', { class: 'none' }, 'Не нашлось ни одного канала.'));
    sayChannel();
  }
  cq.addEventListener('input', () => { clearTimeout(cq._t); cq._t = setTimeout(loadChannels, 300); });

  const f = msgForm(true);
  const when = h('input', { type: 'datetime-local', 'aria-label': 'Когда отправить' });
  when.value = toLocalInput(new Date(Date.now() + 3600 * 1000));
  const repeat = h('select', { 'aria-label': 'Повторять' },
    h('option', { value: '' }, 'Один раз'), h('option', { value: 'daily' }, 'Каждый день'), h('option', { value: 'weekly' }, 'Каждую неделю'));
  const setIn = (sec) => { when.value = toLocalInput(new Date(Date.now() + sec * 1000)); };
  const quick = h('div', { class: 'row' },
    h('button', { type: 'button', class: 'btn', onclick: () => setIn(600) }, '+10 мин'),
    h('button', { type: 'button', class: 'btn', onclick: () => setIn(3600) }, '+1 час'),
    h('button', { type: 'button', class: 'btn', onclick: () => { const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(12, 0, 0, 0); when.value = toLocalInput(d); } }, 'Завтра в 12:00'));
  const res = h('div');
  const submit = h('button', { class: 'btn primary', onclick: async () => {
    const at = Date.parse(when.value) / 1000;
    if (!channelId) { toast('Сначала выбери канал в списке', 'err'); return; }
    if (!(at > 0)) { toast('Укажи дату и время', 'err'); return; }
    const c = chanRows.find((x) => x.id === channelId);
    submit.disabled = true;
    try {
      await api('/api/schedule/add', { method: 'POST', body: {
        channel: channelId, channel_name: c ? c.name : '', text: f.text.value, title: f.title.value, color: f.color.value, author: f.author.value,
        ping: f.ping.checked, speaker: f.speaker(), at, repeat: repeat.value } });
      toast('Запланировано');
      f.text.value = '';
      await load();
    } catch (e) { res.replaceChildren(h('p', { class: 'err' }, e.message)); }
    finally { submit.disabled = false; }
  } }, 'Запланировать');

  const queue = h('div', { class: 'plist' });
  const hist = h('div', { class: 'plist' });
  const hhead = h('div', { class: 'section-title' }, 'Уже отправлено');
  let skew = 0;
  function item(i) {
    const left = i.at - (Date.now() / 1000 + skew);
    const snip = (i.title ? i.title + ' — ' : '') + i.text;
    const el = h('div', { class: 'card plitem ' + i.status },
      h('div', { class: 'plwhen' }, h('b', null, fmtWhen(i.at)),
        i.status === 'wait' ? h('small', { class: 'muted' }, left > 0 ? 'через ' + inText(left) : 'вот-вот') : null),
      h('div', { class: 'plmain' },
        h('div', { class: 'row small' }, chip('#' + (i.channel_name || i.channel)),
          i.repeat ? chip(REPEAT_RU[i.repeat] || i.repeat) : null,
          i.ping ? chip('@everyone', 'err') : null,
          i.status === 'sent' ? chip('отправлено', 'ok') : null,
          i.status === 'fail' ? chip('не вышло', 'err') : null),
        h('p', { class: 'plsnip' }, snip.length > 220 ? snip.slice(0, 220) + '…' : snip),
        i.last_error ? h('p', { class: 'warnline small' }, i.last_error) : null,
        i.status === 'sent' && i.last_link ? linkRow(i.last_link, 'Сообщение в Discord') : null),
      h('button', { class: 'btn' + (i.status === 'wait' ? ' danger' : ''), onclick: async () => {
        if (i.status === 'wait' && !(await confirmBox('Отменить отправку?', 'Сообщение не уйдёт.', 'Отменить', true))) return;
        try { await api('/api/schedule/cancel', { method: 'POST', body: { id: i.id } }); await load(); }
        catch (e) { toast(e.message, 'err'); }
      } }, i.status === 'wait' ? 'Отменить' : 'Убрать'));
    return el;
  }
  async function load() {
    const d = await api('/api/schedule');
    skew = d.now - Date.now() / 1000;
    const wait = d.items.filter((i) => i.status === 'wait'), done = d.items.filter((i) => i.status !== 'wait');
    queue.replaceChildren(...wait.map(item));
    if (!wait.length) queue.append(h('div', { class: 'card empty' }, 'Очередь пуста. Составь сообщение слева и выбери время.'));
    hist.replaceChildren(...done.map(item));
    hhead.style.display = done.length ? '' : 'none';
  }
  await Promise.all([loadChannels(), load()]);
  state.timer = setInterval(() => { load().then(() => markLive(true), () => markLive(false)); }, 15000);
  return h('div', null,
    pageHead('Планировщик', 'Бот сам отправит сообщение в нужный час, даже если вы оффлайн. Проверка очереди раз в 20 секунд.'),
    h('div', { class: 'grid2' },
      h('div', { class: 'card form' }, h('h2', null, '⏰ Новое сообщение'),
        h('label', null, 'Поиск канала', cq), chBox, chLine, f.box,
        h('label', null, 'Когда', when), quick,
        h('label', null, 'Повторять', repeat), res, submit),
      h('div', null, h('div', { class: 'section-title' }, 'В очереди'), queue, hhead, hist)));
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
  const vstat = h('div', { class: 'small muted' });
  let valid = true;
  const title = h('h2', null, 'Выберите файл');
  const saveBtn = h('button', { class: 'btn primary', disabled: true, onclick: () => save() }, 'Сохранить');
  const resetBtn = h('button', { class: 'btn', disabled: true, onclick: () => { area.value = original; markDirty(); err.textContent = ''; } }, 'Отменить правки');
  const buttons = new Map();
  const fmtBtn = h('button', { class: 'btn', disabled: true, onclick: () => {
    try { area.value = JSON.stringify(JSON.parse(area.value), null, 2) + '\n'; markDirty(); toast('Файл выровнен', 'ok'); }
    catch (e) { toast('Нельзя выровнять, в файле ошибка: ' + e.message, 'err'); }
  } }, 'Выровнять');

  function validate() {
    if (current === null) { vstat.textContent = ''; valid = true; return; }
    try {
      JSON.parse(area.value);
      valid = true; vstat.className = 'small ok-text'; vstat.textContent = '✓ JSON корректен';
    } catch (e) {
      valid = false; vstat.className = 'small err-text'; vstat.textContent = '✗ Ошибка в файле: ' + e.message;
    }
    saveBtn.disabled = !(dirty && valid);
  }

  state.isDirty = () => dirty;
  function markDirty() {
    dirty = current !== null && area.value !== original;
    resetBtn.disabled = !dirty;
    validate();
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
      area.value = d.content; area.disabled = false; fmtBtn.disabled = false; err.textContent = '';
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
      h('div', null, h('div', { class: 'row' }, title, h('div', { class: 'spacer' }), fmtBtn, resetBtn, saveBtn), area, vstat, err))
  );
}

/* ---------- Логи ---------- */
async function pageLogs() {
  const src = h('select', null, h('option', { value: 'console' }, 'Консоль бота'), h('option', { value: 'file' }, 'Файл bot.log'));
  const cnt = h('select', null, ['200', '500', '1000'].map((n) => h('option', { value: n }, n + ' строк')));
  const lvl = h('select', null, [['', 'Все строки'], ['e', 'Только ошибки'], ['w', 'Ошибки и предупреждения']].map(([v, l]) => h('option', { value: v }, l)));
  const q = h('input', { type: 'search', placeholder: 'Фильтр по тексту…' });
  const auto = h('input', { type: 'checkbox', checked: true });
  const box = h('div', { class: 'logbox mono' });
  const count = h('span', { class: 'muted small' });
  auto.checked = true;
  let loading = false, lastLines = [];

  const level = (l) => {
    if (/error|ошибк|❌|traceback|exception|critical/i.test(l)) return 'e';
    if (/warn|⚠|внимание/i.test(l)) return 'w';
    if (/✅|успешно/i.test(l)) return 'o';
    return '';
  };
  const pass = (l) => {
    if (!lvl.value) return true;
    const k = level(l);
    return lvl.value === 'e' ? k === 'e' : (k === 'e' || k === 'w');
  };

  function draw() {
    const rows = lastLines.filter(pass);
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    box.replaceChildren(...(rows.length ? rows.map((l) => h('div', { class: 'ln ' + level(l) }, l)) : [h('div', { class: 'ln' }, 'Пусто')]));
    if (atBottom) box.scrollTop = box.scrollHeight;
    count.textContent = rows.length + ' из ' + lastLines.length + ' строк';
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const d = await api('/api/logs?source=' + encodeURIComponent(src.value) + '&lines=' + encodeURIComponent(cnt.value) + '&q=' + encodeURIComponent(q.value.trim()));
      lastLines = d.lines || [];
      draw();
    } catch (e) { /* покажем при следующей попытке */ }
    loading = false;
  }

  const copyBtn = h('button', { class: 'btn', onclick: async () => {
    const text = lastLines.filter(pass).join('\n');
    try { await navigator.clipboard.writeText(text); toast('Скопировано: ' + lastLines.filter(pass).length + ' строк', 'ok'); }
    catch (e) { toast('Браузер не дал доступ к буферу обмена: выделите строки вручную', 'err'); }
  } }, 'Копировать');
  const saveBtn = h('button', { class: 'btn', onclick: () => {
    const blob = new Blob([lastLines.filter(pass).join('\n') + '\n'], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'bot-log-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.txt';
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  } }, 'Скачать');

  src.addEventListener('change', load);
  cnt.addEventListener('change', load);
  lvl.addEventListener('change', draw);
  q.addEventListener('input', () => { clearTimeout(q._t); q._t = setTimeout(load, 300); });
  await load();
  box.scrollTop = box.scrollHeight;
  state.timer = setInterval(() => { if (auto.checked) load(); }, 3000);

  return h('div', null,
    pageHead('Логи', 'Свежие строки внизу. Страница обновляется сама каждые 3 секунды. Если что-то сломалось, ищите здесь «Ошибка» и пришлите эти строки.'),
    h('div', { class: 'toolbar' }, src, cnt, lvl, q,
      h('label', { class: 'row small muted' }, auto, 'Автообновление'),
      h('button', { class: 'btn', onclick: () => { load().then(() => { box.scrollTop = box.scrollHeight; }); } }, 'Обновить'),
      copyBtn, saveBtn, h('div', { class: 'spacer' }), count),
    box
  );
}

/* ---------- Тексты бота ---------- */
/* Ключи берутся из самого кода (utils/web_tools.py их сканирует), поэтому список не
   отстаёт: новый get_text в коге появляется здесь сам. Правим три хранилища:
   config.json["texts"], hall_settings.json и golos_guild.json. */
const TEXT_PLACEHOLDERS = ['{nick}', '{name}', '{mention}', '{server}', '{bot_name}', '{title}',
  '{time}', '{info}', '{static}', '{recruit_channel}', '{reason}', '{rank}', '{days}', '{hours}',
  '{balance}', '{raised}', '{spent}', '{moves}', '{reports}', '{amount}', '{purpose}', '{people}', '{id}', '{made}'];

/* ---------- Эмодзи: общие кусочки ---------- */
/* Каталог берётся с сервера один раз и хранится в памяти страницы: из него строятся и выбор эмодзи
   в любом окне ввода, и предпросмотр (<:имя:id> превращается в настоящую картинку). */
/* Третья форма — токен `:имя:` из пака (его подставляет brand.E в боте): тут он рисуется
   той же картинкой, что и готовый код, чтобы правка текста была видна живой. */
const EMO_RE = /<(a?):([A-Za-z0-9_]{2,32}):(\d{15,25})>|\{e:([A-Za-z0-9_]+)\}|(^|[^<\w]):([A-Za-z0-9_]{2,32}):(?!\d)/g;

function emoUrl(animated, id) {
  return 'https://cdn.discordapp.com/emojis/' + id + '.' + (animated ? 'gif' : 'png') + '?size=48';
}

/* Текст с эмодзи → массив узлов. Весь вывод через createTextNode/img, поэтому HTML из текста не вставится. */
function emojiNodes(str) {
  const out = [];
  let last = 0, m;
  EMO_RE.lastIndex = 0;
  while ((m = EMO_RE.exec(str))) {
    const lead = m[5] ? m[5].length : 0;          // служебный символ перед токеном
    if (m.index + lead > last) out.push(document.createTextNode(str.slice(last, m.index + lead)));
    const name = m[2] || m[4] || m[6];
    let url = null;
    if (m[3]) url = emoUrl(m[1], m[3]);
    else if (state.emojiIdx && state.emojiIdx[name.toLowerCase()]) url = state.emojiIdx[name.toLowerCase()].url;
    out.push(url
      ? h('img', { class: 'emo', src: url, alt: ':' + name + ':', title: ':' + name + ':', referrerpolicy: 'no-referrer' })
      : document.createTextNode(m[0].slice(lead)));
    last = m.index + m[0].length;
  }
  if (last < str.length) out.push(document.createTextNode(str.slice(last)));
  return out;
}

/* **жирный** и эмодзи, как в Discord; остальное остаётся обычным текстом */
function richNode(text) {
  const str = String(text === null || text === undefined ? '' : text);
  const out = [];
  const BOLD = /\*\*([\s\S]+?)\*\*/g;
  let last = 0, m;
  while ((m = BOLD.exec(str))) {
    if (m.index > last) out.push(...emojiNodes(str.slice(last, m.index)));
    out.push(h('strong', null, emojiNodes(m[1])));
    last = m.index + m[0].length;
  }
  if (last < str.length) out.push(...emojiNodes(str.slice(last)));
  return out;
}

function getEmojis(force) {
  if (!state.emojiP || force) {
    state.emojiP = api('/api/emojis').then((d) => {
      state.emojiIdx = {};
      d.rows.forEach((r) => { state.emojiIdx[r.name.toLowerCase()] = r; });
      return d;
    });
    state.emojiP.catch(() => { state.emojiP = null; });
  }
  return state.emojiP;
}

function insertAtCursor(area, text) {
  const from = area.selectionStart === null || area.selectionStart === undefined ? area.value.length : area.selectionStart;
  const to = area.selectionEnd === null || area.selectionEnd === undefined ? from : area.selectionEnd;
  area.value = area.value.slice(0, from) + text + area.value.slice(to);
  const pos = from + text.length;
  area.focus();
  area.setSelectionRange(pos, pos);
  area.dispatchEvent(new Event('input', { bubbles: true }));
}

function uploadSummary(r) {
  const bits = [];
  if (r.uploaded && r.uploaded.length) bits.push('загружено: ' + r.uploaded.length);
  if (r.failed && r.failed.length) bits.push('не вышло: ' + r.failed.length);
  if (r.left) bits.push('осталось в очереди: ' + r.left + ' (нажмите ещё раз)');
  return bits.join(' · ') || r.message || 'Нечего загружать';
}

async function uploadEmojis(names) {
  const r = await api('/api/emojis/upload', { method: 'POST', body: { names } });
  state.emojiP = null;
  return r;
}

function emojiButton(area, label) {
  return h('button', { class: 'btn tiny', type: 'button', title: 'Вставить эмодзи сервера',
    onclick: () => openEmojiPicker((code) => insertAtCursor(area, code)) }, icon('smile'), label || 'Эмодзи');
}

async function openEmojiPicker(onPick) {
  const old = document.getElementById('epick');
  if (old) old.remove();
  const dlg = h('dialog', { id: 'epick', class: 'pal epick' });
  const q = h('input', { type: 'search', placeholder: 'Найти эмодзи…', 'aria-label': 'Поиск эмодзи' });
  const grid = h('div', { class: 'egrid' }, spinner('Загружаю эмодзи…'));
  const foot = h('div', { class: 'efoot' });
  dlg.append(q, grid, foot);
  dlg.addEventListener('close', () => dlg.remove());
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
  document.body.append(dlg);
  dlg.showModal();
  q.focus();
  let data;
  try { data = await getEmojis(); }
  catch (e) { grid.replaceChildren(h('p', { class: 'err' }, e.message)); return; }

  async function choose(r) {
    dlg.close();
    if (r.uploaded) { onPick(r.code); return; }
    if (!data.info.can_manage) { toast('У бота нет права «Управлять эмодзи и стикерами», загрузить нельзя', 'err'); return; }
    if (!(await confirmBox('Загрузить «' + r.name + '» на сервер?', 'Этой картинки ещё нет среди эмодзи сервера. Бот загрузит её из папки emojis, после этого эмодзи можно вставлять в тексты.', 'Загрузить', false))) return;
    try {
      const res = await uploadEmojis([r.name]);
      const fresh = await getEmojis(true);
      const row = fresh.rows.find((x) => x.name.toLowerCase() === r.name.toLowerCase());
      if (row && row.uploaded) { toast('Загружено и вставлено', 'ok'); onPick(row.code); }
      else toast((res.failed && res.failed[0] && res.failed[0].error) || 'Не загрузилось', 'err');
    } catch (e) { toast(e.message, 'err'); }
  }

  function draw() {
    const needle = q.value.trim().toLowerCase();
    const rows = data.rows.filter((r) => !needle || r.name.toLowerCase().includes(needle));
    grid.replaceChildren(...(rows.length ? rows.slice(0, 400).map((r) =>
      h('button', { class: 'ebtn' + (r.uploaded ? '' : ' off'), type: 'button', title: r.name + (r.uploaded ? '' : ' — ещё не на сервере'),
        onclick: () => choose(r) },
        h('img', { src: r.url, alt: r.name, loading: 'lazy', referrerpolicy: 'no-referrer' }),
        h('span', null, r.name)))
      : [h('div', { class: 'none' }, 'Ничего не найдено')]));
    foot.textContent = 'Нажмите на эмодзи, чтобы вставить. Бледные пока лежат только в папке: бот предложит загрузить их на сервер.';
  }
  q.addEventListener('input', draw);
  draw();
}

/* ---------- Страница «Эмодзи» ---------- */
async function pageEmojis() {
  let data = await getEmojis(true);
  const q = h('input', { type: 'search', placeholder: 'Найти эмодзи…', 'aria-label': 'Поиск эмодзи' });
  const filter = h('select', { 'aria-label': 'Показать' }, [['all', 'Все'], ['on', 'Уже на сервере'], ['off', 'Ещё не загружены'], ['anim', 'Анимированные']]
    .map(([v, l]) => h('option', { value: v }, l)));
  const stats = h('div', { class: 'stats' });
  const warn = h('div');
  const grid = h('div', { class: 'egallery' });
  const upBtn = h('button', { class: 'btn primary' }, 'Загрузить недостающие');
  let busy = false;

  async function copy(r) {
    try { await navigator.clipboard.writeText(r.code); toast('Код скопирован: ' + r.name, 'ok'); }
    catch (e) { toast('Браузер не дал доступ к буферу. Код: ' + r.code, 'err'); }
  }

  async function doUpload(names, title) {
    if (busy) return;
    if (!data.info.can_manage) { toast('У бота нет права «Управлять эмодзи и стикерами». Выдайте его роли бота.', 'err'); return; }
    if (!(await confirmBox(title, 'Бот загрузит картинки из папки emojis на сервер. За один раз не больше 20 штук, остальные можно догрузить повторным нажатием. Если на сервере кончатся места, бот остановится и скажет об этом.', 'Загрузить', false))) return;
    busy = true; upBtn.disabled = true;
    try {
      const r = await uploadEmojis(names);
      toast(uploadSummary(r), r.failed && r.failed.length ? 'err' : 'ok');
      if (r.failed && r.failed.length) {
        warn.replaceChildren(h('div', { class: 'card errcard' }, h('h3', null, 'Не всё загрузилось'),
          lines(r.failed.map((f) => f.name + ': ' + f.error), 'steps')));
      } else warn.replaceChildren();
    } catch (e) { toast(e.message, 'err'); }
    busy = false;
    try { data = await getEmojis(true); } catch (e) { /* оставим как есть */ }
    render();
  }

  function render() {
    const i = data.info || {};
    const missStatic = data.rows.filter((r) => !r.uploaded && !r.animated).length;
    const missAnim = data.rows.filter((r) => !r.uploaded && r.animated).length;
    stats.replaceChildren(
      ...[['В папке и на сервере', data.total, ''], ['Уже на сервере', data.total - data.missing, 'ok'],
        ['Ещё не загружены', data.missing, data.missing ? 'warn' : ''],
        ['Места: обычные', i.limit ? (i.static + ' / ' + i.limit) : '—', ''],
        ['Места: анимированные', i.limit ? (i.animated + ' / ' + i.limit) : '—', '']]
        .map(([l, v, k]) => h('div', { class: 'card stat ' + k }, h('small', null, l), h('b', null, String(v)))));
    const notes = [];
    if (i.limit && missStatic > i.limit - i.static) notes.push('Обычных эмодзи не загружено ' + missStatic + ', а свободных мест ' + Math.max(0, i.limit - i.static) + '. Остальные не влезут, пока вы не освободите места или не поднимете уровень сервера.');
    if (i.limit && missAnim > i.limit - i.animated) notes.push('Анимированных не загружено ' + missAnim + ', свободных мест ' + Math.max(0, i.limit - i.animated) + '.');
    if (i.guild && !i.can_manage) notes.push('У бота нет права «Управлять эмодзи и стикерами», поэтому загрузить новые он не сможет.');
    if (!i.guild) notes.push('Бот сейчас не на сервере, поэтому показан только список файлов из папки.');
    upBtn.disabled = busy || !data.missing || !i.can_manage;
    upBtn.textContent = data.missing ? 'Загрузить недостающие (' + data.missing + ')' : 'Всё уже загружено';
    upBtn.onclick = () => doUpload(null, 'Загрузить недостающие эмодзи?');
    const need = q.value.trim().toLowerCase(), f = filter.value;
    const rows = data.rows.filter((r) => (!need || r.name.toLowerCase().includes(need))
      && (f === 'all' || (f === 'on' && r.uploaded) || (f === 'off' && !r.uploaded) || (f === 'anim' && r.animated)));
    grid.replaceChildren(...(rows.length ? rows.map((r) =>
      h('div', { class: 'card ecard' + (r.uploaded ? '' : ' off') },
        h('button', { class: 'eimg', type: 'button', title: r.uploaded ? 'Скопировать код' : 'Ещё не на сервере', onclick: () => { if (r.uploaded) copy(r); else doUpload([r.name], 'Загрузить «' + r.name + '»?'); } },
          h('img', { src: r.url, alt: r.name, loading: 'lazy', referrerpolicy: 'no-referrer' })),
        h('b', { title: r.name }, r.name),
        h('div', { class: 'row small' },
          r.uploaded ? chip('на сервере', 'ok') : chip('в папке', 'off'),
          r.animated ? chip('анимация', 'off') : null),
        r.uploaded
          ? h('button', { class: 'btn tiny', onclick: () => copy(r) }, 'Копировать код')
          : h('button', { class: 'btn tiny', disabled: !i.can_manage, onclick: () => doUpload([r.name], 'Загрузить «' + r.name + '»?') }, 'Загрузить'),
        r.only_server ? h('small', { class: 'muted' }, 'файла в папке нет') : null))
      : [h('p', { class: 'muted' }, 'Ничего не найдено.')]));
    warn.prepend(...notes.map((t) => h('p', { class: 'warnline' }, t)));
  }

  q.addEventListener('input', render);
  filter.addEventListener('change', render);
  render();
  return h('div', null,
    pageHead('Эмодзи сервера', 'Картинки из папки emojis и то, что уже загружено на сервер. Нажмите на эмодзи, чтобы скопировать его код. Во всех окнах ввода есть кнопка «Эмодзи», она вставляет их прямо в текст.',
      h('button', { class: 'btn', onclick: () => go('emojis') }, 'Обновить'), upBtn),
    stats, warn,
    h('div', { class: 'toolbar' }, h('label', null, 'Найти', q), h('label', null, 'Показать', filter)),
    grid);
}

/* ---------- Тексты бота ---------- */
async function pageTexts() {
  const d = await api('/api/texts').catch((e) => ({ error: e.message }));
  if (d.error) return h('div', null, pageHead('Тексты', null), h('p', { class: 'err' }, d.error));
  getEmojis().catch(() => { /* без каталога эмодзи просто остаются кодом */ });

  const items = d.items;
  const search = h('input', { type: 'search', placeholder: 'Поиск: слово из текста, название или раздел (казна, выплаты…)', 'aria-label': 'Поиск надписи' });
  const kindSel = h('select', { 'aria-label': 'Тип надписи' },
    [['', 'Все типы'], ['text', 'Тексты'], ['title', 'Заголовки'], ['button', 'Кнопки'],
     ['field', 'Поля форм'], ['hint', 'Подсказки полей'], ['topic', 'Описания каналов']]
      .map(([v, l]) => h('option', { value: v }, l)));
  const found = h('small', { class: 'muted tcount' });
  const list = h('div', { class: 'tlist' });
  const filters = h('div', { class: 'tfilters' });
  const edBox = h('div', { class: 'card ted' }, h('p', { class: 'muted' }, 'Выберите надпись слева.'));
  let current = null, onlyChanged = false, groupFilter = '', kindFilter = '';

  const titleOf = (it) => it.label || it.key.replace(/_/g, ' ');
  const KIND_LABEL = { title: 'заголовок', text: 'текст', button: 'кнопка', field: 'поле формы',
    hint: 'подсказка поля', topic: 'описание канала' };

  function drawFilters() {
    const sel = h('select', { 'aria-label': 'Раздел', onchange: () => { groupFilter = sel.value; renderList(); } },
      h('option', { value: '' }, 'Все разделы · ' + d.total),
      ...d.groups.map((g) => h('option', { value: g.title }, g.title + ' · ' + g.items.length)));
    sel.value = groupFilter;
    const mine = d.items.filter((x) => x.changed).length;
    const tog = h('button', { class: 'chipbtn' + (onlyChanged ? ' on' : ''), title: 'Показать только то, что вы меняли',
      onclick: () => { onlyChanged = !onlyChanged; drawFilters(); renderList(); } }, 'Мои правки · ' + mine);
    filters.replaceChildren(sel, kindSel, tog);
  }

  function matches(it, words, gTitle) {
    if (onlyChanged && !it.changed) return false;
    if (kindFilter && it.kind !== kindFilter) return false;
    if (!words.length) return true;
    // каждое слово само по себе: «баланс казны» и «казна баланс» ищут одинаково
    const hay = (it.key + ' ' + titleOf(it) + ' ' + (it.value || '') + ' '
      + gTitle + ' ' + it.src + ' ' + (KIND_LABEL[it.kind] || '')).toLowerCase();
    return words.every((w) => hay.includes(w));
  }

  function renderList() {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const cards = [];
    let shown = 0;
    d.groups.forEach((g) => {
      if (groupFilter && g.title !== groupFilter) return;
      const rows = g.items.filter((it) => matches(it, words, g.title));
      if (!rows.length) return;
      shown += rows.length;
      cards.push(h('div', { class: 'section-title' }, g.title + ' · ' + rows.length),
        ...rows.map((it) => h('button', { class: 'r' + (current && current.id === it.id ? ' sel' : ''), onclick: () => open(it) },
          h('div', { class: 'grow' }, h('b', null, titleOf(it)),
            h('small', null, (it.value || '').replace(/\s+/g, ' ').slice(0, 70) || it.key),
            h('small', { class: 'src' }, (KIND_LABEL[it.kind] || it.kind) + ' · ' + it.src)),
          it.changed ? chip('свой', 'ok') : null)));
    });
    found.textContent = 'Найдено надписей: ' + shown;
    list.replaceChildren(...(cards.length ? cards : [h('p', { class: 'muted' },
      words.length || kindFilter ? 'Ничего не нашлось — попробуй другое слово или сними фильтр типа.'
        : 'Список пуст: бот не нашёл надписей в своём коде.')]));
  }

  function previewNode(p) {
    const box = h('div', { class: 'previewbox' });
    if (p.overflow) box.append(h('p', { class: 'err small' }, 'Не влезает: ' + p.len + ' из ' + p.limit + ' знаков'));
    if (p.unknown && p.unknown.length) box.append(h('p', { class: 'warnline small' }, 'Такой подстановки нет: ' + p.unknown.join(', ')));
    let body;
    if (p.kind === 'button') body = h('div', { class: 'btns' }, h('span', { class: 'dcbtn' }, richNode(p.text || '(пусто)')));
    else if (p.kind === 'field') body = h('div', { class: 'dcfield' }, h('label', null, richNode(p.text || '(пусто)')), h('div', { class: 'dcinput' }, 'так видит человек'));
    else if (p.kind === 'hint') body = h('div', { class: 'dcfield' }, h('label', null, 'название поля'), h('div', { class: 'dcinput' }, richNode(p.text || '(пусто)')));
    else if (p.kind === 'topic') body = h('div', { class: 'dctopic' }, richNode(p.text || '(пусто)'));
    else body = embedNode({ title: p.title || '', text: p.text, color: '#ffffff' });
    box.append(h('div', { class: 'section-title' }, 'Как это увидят в Discord'),
      h('div', { class: 'dcprev' },
        h('div', { class: 'dcava' }, (state.botName || 'B').slice(0, 1).toUpperCase()),
        h('div', { class: 'dcbody' },
          h('div', { class: 'dchead' }, h('b', null, state.botName), h('span', { class: 'dctag' }, 'БОТ')),
          body)));
    return box;
  }

  async function showPreview(previewBox, area, counter, meter, it) {
    try {
      const p = await api('/api/texts/preview', { method: 'POST', body: { id: it.id, value: area.value } });
      counter.textContent = p.len + ' / ' + p.limit;
      counter.className = 'small ' + (p.overflow ? 'err-text' : 'muted');
      meter.firstChild.style.width = Math.min(100, Math.round(p.len / p.limit * 100)) + '%';
      meter.className = 'meter' + (p.overflow ? ' bad' : p.len / p.limit > .85 ? ' warn' : '');
      previewBox.replaceChildren(previewNode(p));
    } catch (e) { /* превью не главное: ошибку держим молча */ }
  }

  async function open(it) {
    if (current && current.id === it.id) return;
    if (state.isDirty() && !(await confirmBox('Несохранённые правки', 'Если открыть другую надпись, правки в этой пропадут.', 'Открыть', true))) return;
    current = it;
    renderList();
    const area = h('textarea', { class: 'msg tall', spellcheck: 'false', 'aria-label': 'Текст надписи' });
    area.value = it.value || '';
    const counter = h('small', { class: 'muted' });
    const meter = h('div', { class: 'meter' }, h('i'));
    const previewBox = h('div');
    const status = h('span', { class: 'small muted' });
    const dirty = () => area.value !== (it.value || '');
    const sync = () => {
      state.isDirty = dirty;
      status.textContent = dirty() ? '● Не сохранено' : 'Всё сохранено';
      status.className = 'small ' + (dirty() ? 'warnline' : 'muted');
    };
    state.isDirty = dirty;
    area.addEventListener('input', () => {
      sync();
      clearTimeout(area._t);
      area._t = setTimeout(() => showPreview(previewBox, area, counter, meter, it), 300);
    });

    const phSelect = h('select', { 'aria-label': 'Вставить подстановку' },
      h('option', { value: '' }, 'Подстановка…'),
      TEXT_PLACEHOLDERS.map((ph) => h('option', { value: ph }, ph)));
    phSelect.addEventListener('change', () => { if (phSelect.value) insertAtCursor(area, phSelect.value); phSelect.value = ''; });

    const saveBtn = h('button', { class: 'btn primary' }, 'Сохранить');
    async function save() {
      saveBtn.disabled = true;
      try {
        const r = await api('/api/texts/save', { method: 'POST', body: { id: it.id, value: area.value } });
        it.value = area.value;
        it.changed = area.value !== (it.default || '');
        sync(); renderList(); drawFilters();
        toast('Сохранено: ' + (r.saved || it.key), 'ok');
      } catch (e) { toast(e.message, 'err'); }
      saveBtn.disabled = false;
    }
    saveBtn.onclick = save;
    area.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); }
    });

    const resetBtn = h('button', { class: 'btn', onclick: async () => {
      if (!(await confirmBox('Вернуть текст из кода?', 'Ваша правка этой надписи сотрётся.', 'Вернуть', true))) return;
      try {
        const r = await api('/api/texts/reset', { method: 'POST', body: { id: it.id } });
        it.value = r.value || ''; it.changed = false; area.value = it.value;
        sync(); renderList(); drawFilters();
        showPreview(previewBox, area, counter, meter, it);
        toast('Как в коде', 'ok');
      } catch (e) { toast(e.message, 'err'); }
    } }, 'Как в коде');
    const undoBtn = h('button', { class: 'btn', onclick: () => { area.value = it.value || ''; area.dispatchEvent(new Event('input')); } }, 'Отменить');

    edBox.replaceChildren(
      h('div', { class: 'thead' },
        h('div', null, h('h2', null, titleOf(it)), h('small', { class: 'muted mono' }, it.key),
          h('div', { class: 'row small muted' }, it.group, ' · ', h('span', { class: 'mono' }, it.src))),
        h('div', { class: 'row' }, chip(KIND_LABEL[it.kind] || it.kind), chip('до ' + it.limit + ' знаков', 'off'),
          it.changed ? chip('свой текст', 'ok') : chip('как в коде', 'off'))),
      h('div', { class: 'ttools' }, emojiButton(area), phSelect, h('div', { class: 'spacer' }), counter),
      meter,
      area,
      previewBox,
      h('div', { class: 'actbar' }, status, h('div', { class: 'spacer' }), undoBtn, resetBtn, saveBtn));
    sync();
    showPreview(previewBox, area, counter, meter, it);
  }

  search.addEventListener('input', renderList);
  kindSel.addEventListener('change', () => { kindFilter = kindSel.value; renderList(); });

  drawFilters();
  renderList();
  const first = items.find((it) => it.changed) || items[0];
  if (first) await open(first);

  return h('div', null,
    pageHead('Тексты бота', 'Все надписи бота. Слева выбирай, справа правь — рядом сразу видно, как это будет в Discord. Ctrl+S сохраняет.',
      h('button', { class: 'btn ghost', title: 'Перечитать список надписей с бота', onclick: () => go('texts') }, 'Свежий список')),
    h('div', { class: 'split tsplit' },
      h('div', { class: 'tside' },
        h('div', { class: 'tsearch' }, search),
        filters, found, h('div', { class: 'tscroll' }, list)),
      edBox));
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
  const [p, ov] = await Promise.all([api('/api/presence'), api('/api/overview').catch(() => null)]);
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

  const sleepMin = h('input', { type: 'number', min: '1', max: '720', placeholder: 'мин', 'aria-label': 'Своё число минут', style: 'width:5.5rem' });
  const tSleep = tile('💤', 'Сон по таймеру',
    'Ночью бот только расходует трафик хостинга. Поставь таймер — он выключится сам; поднимать обратно придётся кнопкой на хостинге.', 'warn-tile',
    h('button', { class: 'btn', onclick: () => setSleep(15) }, '15 мин'),
    h('button', { class: 'btn', onclick: () => setSleep(60) }, '1 час'),
    sleepMin,
    h('button', { class: 'btn', onclick: () => { const m = parseInt(sleepMin.value, 10); if (m >= 1 && m <= 720) setSleep(m); else toast('Введите число минут от 1 до 720', 'err'); } }, 'Свой срок'),
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

  const strip = ov ? h('div', { class: 'stats' }, [
    ['Статус', ov.ready ? 'В сети' : 'Запускается', ov.ready ? 'ok' : 'warn'],
    ['Пинг', ov.latency_ms == null ? '—' : ov.latency_ms + ' мс', ''],
    ['Работает', fmtUptime(ov.uptime_s), ''],
    ['Память', ov.memory_mb == null ? '—' : ov.memory_mb + ' МБ', ''],
  ].map(([l, v, k]) => h('div', { class: 'card stat ' + k }, h('small', null, l), h('b', null, v)))) : null;

  return h('div', null,
    pageHead('Управление', 'Четыре блока: состояние, быстрые действия, питание и статус бота в Discord.',
      h('button', { class: 'btn', onclick: () => go('control') }, 'Обновить')),
    strip,
    h('div', { class: 'section-title' }, 'Быстрые действия'),
    h('div', { class: 'tiles' }, tReload, tHide, tShow, tPerms),
    h('div', { class: 'section-title' }, 'Питание'),
    h('div', { class: 'cols' },
      tSleep,
      h('div', { class: 'card form danger-zone' },
        h('h2', null, 'Перезапуск и выключение'),
        h('p', { class: 'muted small' }, 'Перезапуск помогает, если бот завис или вы изменили настройки, которые читаются только при старте. Код модулей можно применить и без рестарта, кнопкой выше.'),
        h('div', { class: 'row' }, restart, stop))),
    h('div', { class: 'section-title' }, 'Статус в Discord'),
    h('div', { class: 'card form', style: 'max-width:560px' },
      h('label', null, 'Состояние', status),
      h('label', null, 'Активность', kind),
      h('label', null, 'Текст (пусто, чтобы убрать)', text),
      h('div', { class: 'row' }, apply),
      h('p', { class: 'muted small' }, 'Статус сохраняется и применяется после каждого запуска бота.'))
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
const COLOR_PRESETS = ['ffffff', '990000', 'e11d48', 'f59e0b', '22c55e', '3b82f6', '8b5cf6', '111111'];
function hexOf(v) {
  const x = String(v || '').replace('#', '').trim();
  return /^([0-9a-f]{3}|[0-9a-f]{6})$/i.test(x) ? '#' + x : '#ffffff';
}

/* Редактор сообщения: поля слева направо по смыслу + живой предпросмотр «как в Discord» */
function msgForm(needPing) {
  const author = h('input', { type: 'text', maxlength: '100', placeholder: 'Например: Штаб семьи', 'aria-label': 'От кого' });
  const title = h('input', { type: 'text', maxlength: '256', placeholder: 'Заголовок (можно оставить пустым)', 'aria-label': 'Заголовок' });
  const text = h('textarea', { class: 'msg', placeholder: 'Напиши сообщение здесь…', 'aria-label': 'Текст сообщения' });
  const color = h('input', { type: 'text', maxlength: '9', class: 'hexin', value: 'ffffff', 'aria-label': 'Цвет полоски, например 990000' });
  const ping = h('input', { type: 'checkbox' });
  const count = h('small', { class: 'muted' }, '0 / 4000');
  const prev = h('div', { class: 'dcprev' });
  /* от чьего имени писать: сам бот, подпись и аватар участника или любое своё имя (через вебхук) */
  let spMode = 'bot';
  const spSeg = h('div', { class: 'seg small', role: 'tablist' });
  const spName = h('input', { type: 'text', maxlength: '80', placeholder: 'Имя, например: Глава семьи', 'aria-label': 'Имя отправителя' });
  const spAva = h('input', { type: 'text', maxlength: '500', placeholder: 'Ссылка на аватарку https://… (необязательно)', 'aria-label': 'Аватарка' });
  const spQ = h('input', { type: 'search', placeholder: 'Найти участника по нику…', 'aria-label': 'Поиск участника' });
  const spList = h('div', { class: 'avlist', role: 'listbox', 'aria-label': 'Участники сервера' });
  let spId = '', spWho = '', spPic = '';
  const spBox = h('div', { class: 'spbox' });
  let spLoaded = false;
  const avOk = (u) => typeof u === 'string' && u.startsWith('https://cdn.discordapp.com/');
  function drawList(rows) {
    spList.replaceChildren(...(rows.length ? rows.map((m) => h('button', {
      type: 'button', role: 'option', class: 'avrow' + (m.id === spId ? ' on' : ''), 'aria-selected': m.id === spId ? 'true' : 'false',
      onclick: () => { spId = m.id; spWho = m.name; spPic = avOk(m.avatar) ? m.avatar : ''; drawList(rows); upd(); } },
      avOk(m.avatar) ? h('img', { src: m.avatar, alt: '', referrerpolicy: 'no-referrer' }) : h('span', { class: 'avph' }, (m.name || '?').slice(0, 1).toUpperCase()),
      h('span', { class: 'grow' }, h('b', null, m.name), h('small', null, m.top || 'без роли')))) : [h('div', { class: 'none' }, 'Никого не нашлось.')]));
  }
  async function spLoad() {
    spLoaded = true;
    try { drawList((await api('/api/members?q=' + encodeURIComponent(spQ.value.trim()) + '&limit=60')).rows); }
    catch (e) { toast(e.message, 'err'); }
  }
  spQ.addEventListener('input', () => { clearTimeout(spQ._t); spQ._t = setTimeout(spLoad, 300); });
  function drawSp() {
    spSeg.replaceChildren(...[['bot', 'Бот'], ['member', 'От человека'], ['custom', 'Своё имя']].map(([k, l]) => h('button', {
      type: 'button', class: k === spMode ? 'on' : '', onclick: () => { spMode = k; drawSp(); upd(); } }, l)));
    const note = h('small', null, spMode === 'bot' ? 'Сообщение придёт от самого бота.'
      : 'Боту нужно право «Управлять вебхуками» в канале. Значок БОТ рядом с ником убрать нельзя: его ставит сам Discord на все сообщения не от живого человека.');
    spBox.replaceChildren(...(spMode === 'member' ? [spQ, spList, note] : spMode === 'custom' ? [spName, spAva, note] : [note]));
    if (spMode === 'member' && !spLoaded) spLoad();
  }
  function speaker() {
    if (!needPing) return null;
    if (spMode === 'member' && spId) return { member: spId };
    if (spMode === 'custom' && spName.value.trim()) return { name: spName.value.trim(), avatar: spAva.value.trim() };
    return null;
  }
  function speakerName() {
    if (spMode === 'member' && spId) return spWho;
    if (spMode === 'custom' && spName.value.trim()) return spName.value.trim();
    return state.botName;
  }
  const swatches = h('div', { class: 'swatches' }, COLOR_PRESETS.map((c) => h('button', {
    type: 'button', class: 'sw', title: '#' + c, 'aria-label': 'Цвет ' + c, style: 'background:#' + c,
    onclick: () => { color.value = c; upd(); } })));
  function upd() {
    const t = text.value, ti = title.value.trim(), au = author.value.trim();
    count.textContent = t.length + ' / 4000';
    count.className = t.length > 4000 ? 'err-text' : 'muted';
    const body = (ti || au)
      ? embedNode({ author: au, title: ti, text: t || 'Здесь будет текст сообщения', color: hexOf(color.value) })
      : h('div', { class: 'dctext' }, richNode(t || 'Здесь будет текст сообщения'));
    const who = speakerName() || 'B';
    const pic = spMode === 'member' && spId ? spPic : (spMode === 'custom' && /^https:\/\/cdn\.discordapp\.com\//.test(spAva.value.trim()) ? spAva.value.trim() : '');
    prev.replaceChildren(h('div', { class: 'dcava' }, pic ? h('img', { src: pic, alt: '', referrerpolicy: 'no-referrer' }) : who.slice(0, 1).toUpperCase()),
      h('div', { class: 'dcbody' }, h('div', { class: 'dchead' }, h('b', null, who), h('span', { class: 'dctag' }, 'БОТ')), body,
        needPing && ping.checked ? h('div', { class: 'dcping' }, '@everyone') : null));
  }
  [author, title, text, color, spName, spAva].forEach((el) => el.addEventListener('input', upd));
  drawSp();
  ping.addEventListener('change', upd);
  upd();
  // по умолчанию сообщение уходит обычным текстом; рамка (embed) включается только по желанию
  const frameBox = h('div', { class: 'framebox' },
    h('div', { class: 'cfield' }, h('label', null, 'Заголовок'), title),
    h('div', { class: 'cfield' }, h('label', null, 'Подпись над текстом'), author),
    h('div', { class: 'cfield' }, h('label', null, 'Цвет полоски'),
      h('div', { class: 'row' }, swatches, h('span', { class: 'hash' }, '#'), color)));
  frameBox.style.display = 'none';
  const frame = h('input', { type: 'checkbox', onchange: () => {
    frameBox.style.display = frame.checked ? '' : 'none';
    if (!frame.checked) { title.value = ''; author.value = ''; }
    upd();
  } });
  const box = h('div', { class: 'compose' },
    needPing ? h('div', { class: 'cfield' }, h('label', null, 'Писать от имени'), spSeg, spBox) : null,
    h('div', { class: 'cfield' },
      h('div', { class: 'row between' }, h('label', null, 'Текст'), h('span', { class: 'row' }, emojiButton(text), count)), text),
    h('label', { class: 'row small tick' }, frame, 'Оформить в рамку: заголовок, подпись и цвет (без галочки — обычное сообщение)'),
    frameBox,
    needPing ? h('label', { class: 'row small tick' }, ping, 'Отметить @everyone (все получат уведомление)') : null,
    h('div', { class: 'cfield' }, h('label', null, 'Так это увидят в Discord'), prev));
  return { box, author, title, color, text, ping, speaker };
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

function memberPicker(multi) {
  const q = h('input', { type: 'search', placeholder: 'Ник или ID…', 'aria-label': 'Поиск участника' });
  const box = h('div', { class: 'picks tall' });
  const line = h('div', { class: 'pickline' });
  const chosen = new Set(), names = new Map();
  let rows = [];
  const uid = 'mp' + Math.random().toString(36).slice(2, 7);
  function say() {
    const one = [...chosen][0];
    line.replaceChildren(h('span', null, multi ? 'Выбрано: ' : 'Кому: '),
      h('b', null, multi ? chosen.size + ' чел.' : (one ? (names.get(one) || one) : 'пока никто')));
  }
  function draw() {
    box.replaceChildren(...rows.map((m) => {
      const props = { type: multi ? 'checkbox' : 'radio', checked: chosen.has(m.id), value: m.id,
        onchange: (e) => {
          if (multi) { e.target.checked ? chosen.add(m.id) : chosen.delete(m.id); }
          else { chosen.clear(); chosen.add(m.id); }
          say();
        } };
      if (!multi) props.name = uid;
      return h('label', null, h('input', props),
        h('span', { class: 'grow' }, h('b', null, m.name), h('small', null, memSub(m))),
        chip(m.top || 'без роли', m.top ? 'ok' : 'off'));
    }));
    if (!rows.length) box.append(h('div', { class: 'none' }, 'Никого не нашлось.'));
    say();
  }
  async function load() {
    const d = await api('/api/members?q=' + encodeURIComponent(q.value.trim()) + '&limit=120');
    rows = d.rows; rows.forEach((m) => names.set(m.id, m.name)); draw();
  }
  q.addEventListener('input', () => { clearTimeout(q._t); q._t = setTimeout(load, 300); });
  const node = h('div', { class: 'card form pickcard' }, h('h2', null, multi ? 'Кого включить' : 'Кому написать'), q, box, line,
    multi ? h('div', { class: 'row' },
      h('button', { class: 'btn', onclick: () => { rows.forEach((m) => chosen.add(m.id)); draw(); } }, 'Выбрать всех в списке'),
      h('button', { class: 'btn', onclick: () => { chosen.clear(); draw(); } }, 'Снять всех')) : null);
  return { node, load, ids: () => [...chosen], first: () => [...chosen][0] || '' };
}

async function pageSend() {
  /* ---- в канал ---- */
  const cq = h('input', { type: 'search', placeholder: 'Найти канал…', 'aria-label': 'Поиск канала' });
  const chBox = h('div', { class: 'picks tall' });
  const chLine = h('div', { class: 'pickline target' });
  let channelId = '', chanRows = [];
  function sayChannel() {
    const c = chanRows.find((x) => x.id === channelId);
    chLine.replaceChildren(h('span', null, 'Куда: '), h('b', null, c ? chanLabel(c) : 'канал не выбран — нажми на строчку слева'));
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
      c.can_write === false ? chip('нет прав', 'err') : (c.is_review ? chip('заявки', 'err') : null))));
    if (!d.rows.length) chBox.append(h('div', { class: 'none' }, 'Не нашлось ни одного канала.'));
    sayChannel();
  }
  cq.addEventListener('input', () => { clearTimeout(cq._t); cq._t = setTimeout(loadChannels, 300); });
  const f1 = msgForm(true), r1 = resultBox();
  const sendCh = h('button', { class: 'btn primary big', onclick: async () => {
    if (!channelId) { r1.err(new Error('Сначала выбери канал в списке слева')); return; }
    r1.busy();
    try {
      r1.show(await api('/api/send', { method: 'POST', body: { channel: channelId, text: f1.text.value, title: f1.title.value,
        color: f1.color.value, ping: f1.ping.checked, author: f1.author.value, speaker: f1.speaker() } }));
      toast('Отправлено', 'ok');
    } catch (e) { r1.err(e); }
  } }, 'Отправить в канал');
  const paneChan = h('div', { class: 'sendgrid' },
    h('div', { class: 'card form pickcard' }, h('h2', null, 'Выбери канал'), cq, chBox),
    h('div', { class: 'card compose-card' }, chLine, f1.box,
      h('div', { class: 'row' }, sendCh, h('button', { class: 'btn', onclick: () => go('plan') }, 'Отправить позже…')), r1));

  /* ---- в личку ---- */
  const dm = memberPicker(false);
  const f2 = msgForm(false), r2 = resultBox();
  const sendDm = h('button', { class: 'btn primary big', onclick: async () => {
    if (!dm.first()) { r2.err(new Error('Сначала выбери человека в списке слева')); return; }
    r2.busy();
    try {
      r2.show(await api('/api/dm', { method: 'POST', body: { user: dm.first(), text: f2.text.value, title: f2.title.value,
        color: f2.color.value, author: f2.author.value } }));
      toast('Отправлено', 'ok');
    } catch (e) { r2.err(e); }
  } }, 'Отправить в личку');
  const paneDm = h('div', { class: 'sendgrid' }, dm.node, h('div', { class: 'card compose-card' }, f2.box, sendDm, r2));

  /* ---- рассылка ---- */
  const bc = memberPicker(true);
  const f3 = msgForm(false), r3 = resultBox();
  const bcBody = (dry) => ({ users: bc.ids(), text: f3.text.value, title: f3.title.value, color: f3.color.value,
    author: f3.author.value, dry });
  const paneBc = h('div', { class: 'sendgrid' }, bc.node, h('div', { class: 'card compose-card' }, f3.box,
    h('div', { class: 'row' },
      h('button', { class: 'btn big', onclick: async () => {
        r3.busy();
        try { r3.show(await api('/api/broadcast', { method: 'POST', body: bcBody(true) })); } catch (e) { r3.err(e); }
      } }, 'Проверить без отправки'),
      h('button', { class: 'btn danger big', onclick: async () => {
        if (!(await confirmBox('Рассылка в ЛС?', 'Сообщение уйдёт ' + bc.ids().length + ' людям. Отменить отправку нельзя.', 'Разослать', true))) return;
        r3.busy();
        try { r3.show(await api('/api/broadcast', { method: 'POST', body: bcBody(false) })); toast('Рассылка ушла', 'ok'); } catch (e) { r3.err(e); }
      } }, 'Разослать')), r3));

  const panes = { chan: paneChan, dm: paneDm, bc: paneBc };
  const body = h('div');
  const tabs = h('div', { class: 'seg', role: 'tablist' });
  function show(m) {
    state.sendMode = m;
    tabs.replaceChildren(...[['chan', 'В канал'], ['dm', 'В личку'], ['bc', 'Рассылка']].map(([k, l]) => h('button', {
      class: k === m ? 'on' : '', role: 'tab', 'aria-selected': k === m ? 'true' : 'false', onclick: () => show(k) }, l)));
    body.replaceChildren(panes[m]);
  }
  show(panes[state.sendMode] ? state.sendMode : 'chan');
  await Promise.all([loadChannels(), dm.load(), bc.load()]);

  return h('div', null,
    pageHead('Сообщения', 'Можно писать от имени бота или от любого человека с сервера: выбери аккаунт, и сообщение выйдет с его ником и аватаркой.',
      h('button', { class: 'btn', onclick: () => go('send') }, 'Обновить')),
    tabs, body);
}


/* ---------- Экраны команд ---------- */
function embedNode(e) {
  const box = h('div', { class: 'embed', style: 'border-left-color:' + (e.color || '#990000') });
  if (e.author) box.append(h('small', { class: 'muted' }, e.author));
  if (e.title) box.append(h('b', null, richNode(e.title)));
  if (e.text) box.append(h('div', null, richNode(e.text)));
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
  bindHotkeys();
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

/* ---------- Казна: отчёт со скринами ---------- */
async function apiForm(path, form) {
  /* Тот же api(), но с FormData: файлы в JSON не влезают. */
  let res;
  try {
    res = await fetch(path, { method: 'POST', body: form, credentials: 'same-origin',
      cache: 'no-store', headers: { 'X-Requested-With': 'hz-panel' } });
  } catch (e) { throw new Error('Нет связи с панелью'); }
  let data = null;
  try { data = await res.json(); } catch (e) { /* пустой ответ */ }
  if (res.status === 401) { showLogin('Сессия истекла, войдите снова'); throw new Error('Нужен вход'); }
  if (!res.ok) {
    const fallback = res.status === 403 ? 'Нет прав на это действие'
      : res.status >= 500 ? 'Внутренняя ошибка бота (' + res.status + ')' : 'Ошибка ' + res.status;
    throw new Error((data && data.error) || fallback);
  }
  return data;
}

async function pageKazna() {
  const rep = await api('/api/kazna/reporters');
  if (rep.error) {
    return h('div', null, pageHead('Казна', null), h('p', { class: 'err' }, rep.error));
  }
  const who = h('select', null, rep.rows.length
    ? rep.rows.map((p) => h('option', { value: p.id }, p.name))
    : [h('option', { value: '' }, 'нет людей с правом отчёта')]);
  const sum = h('input', { type: 'text', maxlength: '20', placeholder: '15 000 или 15к' });
  const what = h('input', { type: 'text', maxlength: '200', placeholder: 'Форма, транспорт, печать афиш' });
  const to = h('input', { type: 'text', maxlength: '200', placeholder: 'На кого и на сколько человек' });
  const det = h('textarea', { class: 'msg', placeholder: 'Где брали, кто платил, почему столько (необязательно)' });
  const file = h('input', { type: 'file', accept: 'image/*', multiple: true, style: 'display:none' });
  const drop = h('div', { class: 'drop' },
    h('div', null, '📎  ', h('b', null, 'Перетащите сюда чек или скрин перевода')),
    h('div', { class: 'muted small' }, 'до 3 файлов, не больше 8 МБ каждый · или нажми, чтобы выбрать'));
  const shots = h('div', { class: 'shots' });
  const out = h('div');
  let files = [];

  function draw() {
    shots.replaceChildren(...files.map((f, i) => h('figure', null,
      h('img', { src: URL.createObjectURL(f), alt: f.name }),
      h('button', { title: 'Убрать', onclick: () => { files.splice(i, 1); draw(); } }, '×'),
      h('figcaption', null, Math.round(f.size / 1024) + ' КБ'))));
  }
  function add(list) {
    for (const f of list) {
      if (files.length >= 3) { toast('Больше трёх скринов не нужно', 'err'); break; }
      if (!(f.type || '').startsWith('image/')) { toast('«' + f.name + '» — не картинка', 'err'); continue; }
      if (f.size > 8 * 1024 * 1024) { toast('«' + f.name + '» больше 8 МБ', 'err'); continue; }
      files.push(f);
    }
    draw();
  }
  drop.addEventListener('click', () => file.click());
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); add(e.dataTransfer.files); });
  file.addEventListener('change', () => { add(file.files); file.value = ''; });

  const send = h('button', { class: 'btn primary', onclick: async () => {
    if (!who.value) { toast('Выбери, кто подаёт отчёт', 'err'); return; }
    if (!sum.value.trim() || !what.value.trim()) { toast('Нужны сумма и статья траты', 'err'); return; }
    send.disabled = true;
    out.replaceChildren(spinner('Отправляю…'));
    const fd = new FormData();
    fd.append('actor', who.value);
    fd.append('сумма', sum.value);
    fd.append('на_что', what.value);
    fd.append('кому', to.value);
    fd.append('детали', det.value);
    files.forEach((f) => fd.append('скрин', f, f.name));
    try {
      const r = await apiForm('/api/kazna/report', fd);
      out.replaceChildren(h('p', null, chip('отчёт #' + r.rid, 'ok'),
        ' Ушёл в приёмку: ' + r.review + ' · скринов ' + r.photos +
        ' · сумма спишется после одобрения.'));
      toast('Отчёт #' + r.rid + ' отправлен', 'ok');
      sum.value = what.value = to.value = det.value = '';
      files = [];
      draw();
    } catch (e) {
      out.replaceChildren(h('p', { class: 'err' }, e.message));
      toast(e.message, 'err');
    }
    send.disabled = false;
  } }, 'Отправить отчёт');

  return h('div', null,
    pageHead('Казна: отчёт о трате',
      'Скрины перетаскиваются прямо в форму — как в Discord, только без отдельной команды. '
      + 'Отчёт уйдёт в приёмку, деньги спишутся после одобрения.',
      h('button', { class: 'btn', onclick: () => go('kazna') }, 'Обновить')),
    h('div', { class: 'card form' },
      h('h2', null, '💰 Что потратили'),
      h('label', null, 'Кто подаёт', who),
      h('div', { class: 'grid2' }, h('label', null, 'Сумма', sum), h('label', null, 'На что', what)),
      h('label', null, 'Кому', to),
      h('label', null, 'Детали', det),
      h('label', null, 'Скрины', drop, file, shots),
      h('div', { class: 'row end' }, send),
      out));
}

window.addEventListener('beforeunload', (e) => { if (state.isDirty()) { e.preventDefault(); e.returnValue = ''; } });
applyTheme();
boot();
})();
</script>
</body>
</html>
"""
