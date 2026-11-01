/* ============================================================
   Hallez Music — сайт, который играет под бит
   - Web Audio: 3 встроенных бита (синтез, без внешних файлов)
   - можно загрузить свой трек (📁) — фон будет играть под него
   - анализатор → детектор бита → CSS-переменные --hue/--bass/--pulse
   ============================================================ */
(() => {
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const root = document.documentElement;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const mtof = m => 440 * Math.pow(2, (m - 69) / 12);
const fmt = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

/* ---------- Мягкий режим (меньше вспышек) ---------- */
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let soft = reduceMotion;
try { const saved = localStorage.getItem('hallez-soft'); if (saved !== null) soft = saved === '1'; } catch (_) {}

/* ============================================================
   Аудио-движок
   ============================================================ */
let ctx = null, master, analyser, freq, wave, noiseBuf;
let volume = 0.7, muted = false;

function ensureAudio() {
    if (ctx) { if (ctx.state === 'suspended') ctx.resume(); return; }
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    ctx = new AC();
    master = ctx.createGain();
    master.gain.value = muted ? 0 : volume;

    analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.smoothingTimeConstant = 0.72;
    freq = new Uint8Array(analyser.frequencyBinCount);
    wave = new Uint8Array(analyser.fftSize);

    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -14; comp.ratio.value = 6;
    master.connect(comp); comp.connect(analyser); analyser.connect(ctx.destination);

    // белый шум для снейра/хэта
    noiseBuf = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
    const d = noiseBuf.getChannelData(0);
    for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
}

/* ---------- Инструменты ---------- */
function env(g, t, a, peak, d) {
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(peak, t + a);
    g.gain.exponentialRampToValueAtTime(0.0001, t + a + d);
}
const inst = {
    kick(t, v = 1) {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = 'sine';
        o.frequency.setValueAtTime(160, t);
        o.frequency.exponentialRampToValueAtTime(42, t + 0.12);
        env(g, t, 0.003, 1.0 * v, 0.34);
        o.connect(g); g.connect(master); o.start(t); o.stop(t + 0.4);
    },
    snare(t, v = 1) {
        const n = ctx.createBufferSource(); n.buffer = noiseBuf;
        const f = ctx.createBiquadFilter(); f.type = 'highpass'; f.frequency.value = 1400;
        const g = ctx.createGain(); env(g, t, 0.002, 0.55 * v, 0.18);
        n.connect(f); f.connect(g); g.connect(master); n.start(t); n.stop(t + 0.25);
        const o = ctx.createOscillator(), og = ctx.createGain();
        o.type = 'triangle'; o.frequency.setValueAtTime(220, t); o.frequency.exponentialRampToValueAtTime(120, t + 0.08);
        env(og, t, 0.002, 0.4 * v, 0.1);
        o.connect(og); og.connect(master); o.start(t); o.stop(t + 0.15);
    },
    clap(t, v = 1) {
        for (let i = 0; i < 3; i++) {
            const tt = t + i * 0.012;
            const n = ctx.createBufferSource(); n.buffer = noiseBuf;
            const f = ctx.createBiquadFilter(); f.type = 'bandpass'; f.frequency.value = 1800; f.Q.value = 0.9;
            const g = ctx.createGain(); env(g, tt, 0.002, 0.45 * v, i === 2 ? 0.2 : 0.02);
            n.connect(f); f.connect(g); g.connect(master); n.start(tt); n.stop(tt + 0.25);
        }
    },
    hat(t, v = 1, open = false) {
        const n = ctx.createBufferSource(); n.buffer = noiseBuf;
        const f = ctx.createBiquadFilter(); f.type = 'highpass'; f.frequency.value = 7500;
        const g = ctx.createGain(); env(g, t, 0.001, 0.22 * v, open ? 0.22 : 0.045);
        n.connect(f); f.connect(g); g.connect(master); n.start(t); n.stop(t + 0.3);
    },
    tom(t, v = 1) {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = 'sine'; o.frequency.setValueAtTime(190, t); o.frequency.exponentialRampToValueAtTime(90, t + 0.25);
        env(g, t, 0.004, 0.7 * v, 0.3);
        o.connect(g); g.connect(master); o.start(t); o.stop(t + 0.4);
    },
    bass(t, midi = 36, dur = 0.3, v = 1, type = 'sawtooth') {
        const o = ctx.createOscillator(), f = ctx.createBiquadFilter(), g = ctx.createGain();
        o.type = type; o.frequency.value = mtof(midi);
        f.type = 'lowpass'; f.Q.value = 6;
        f.frequency.setValueAtTime(900, t); f.frequency.exponentialRampToValueAtTime(140, t + dur);
        env(g, t, 0.008, 0.5 * v, dur);
        o.connect(f); f.connect(g); g.connect(master); o.start(t); o.stop(t + dur + 0.1);
    },
    sub(t, midi = 33, dur = 0.5, v = 1) {   // 808
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = 'sine';
        o.frequency.setValueAtTime(mtof(midi) * 2, t);
        o.frequency.exponentialRampToValueAtTime(mtof(midi), t + 0.05);
        env(g, t, 0.004, 0.95 * v, dur);
        o.connect(g); g.connect(master); o.start(t); o.stop(t + dur + 0.1);
    },
    pluck(t, midi, dur = 0.18, v = 1, type = 'square') {
        const o = ctx.createOscillator(), f = ctx.createBiquadFilter(), g = ctx.createGain();
        o.type = type; o.frequency.value = mtof(midi);
        f.type = 'lowpass'; f.frequency.setValueAtTime(5000, t); f.frequency.exponentialRampToValueAtTime(500, t + dur);
        env(g, t, 0.003, 0.16 * v, dur);
        o.connect(f); f.connect(g); g.connect(master); o.start(t); o.stop(t + dur + 0.05);
    },
    pad(t, midis, dur = 1.6, v = 1) {
        midis.forEach(m => {
            const o = ctx.createOscillator(), f = ctx.createBiquadFilter(), g = ctx.createGain();
            o.type = 'sawtooth'; o.frequency.value = mtof(m); o.detune.value = (Math.random() - 0.5) * 14;
            f.type = 'lowpass'; f.frequency.value = 1400;
            g.gain.setValueAtTime(0.0001, t);
            g.gain.exponentialRampToValueAtTime(0.07 * v, t + 0.25);
            g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
            o.connect(f); f.connect(g); g.connect(master); o.start(t); o.stop(t + dur + 0.1);
        });
    },
    zap(t, v = 1) {
        const o = ctx.createOscillator(), g = ctx.createGain();
        o.type = 'sawtooth';
        o.frequency.setValueAtTime(2200, t); o.frequency.exponentialRampToValueAtTime(120, t + 0.28);
        env(g, t, 0.003, 0.22 * v, 0.3);
        o.connect(g); g.connect(master); o.start(t); o.stop(t + 0.35);
    }
};

/* ---------- Треки (синтез) ---------- */
const TRACKS = [
    {
        title: 'Neon Drive', artist: 'Hallez Beats', bpm: 124, hue: 285, bars: 8,
        step(i, bar, t, sd) {
            const roots = [45, 41, 48, 43];          // Am F C G
            const r = roots[bar % 4];
            if (i % 4 === 0) { inst.kick(t); beatAt(t, 1); }
            if (i === 4 || i === 12) inst.clap(t, 0.9);
            if (i % 4 === 2) inst.hat(t, 1, true); else if (i % 2 === 0) inst.hat(t, 0.35);
            if (i % 4 === 2) inst.bass(t, r - 12, sd * 1.6, 1);
            if (i % 2 === 0) inst.pluck(t, r + 24 + [0, 7, 12, 15][(i / 2) % 4], 0.16, 0.8);
            if (i === 0) inst.pad(t, [r + 12, r + 15, r + 19], sd * 16);
        }
    },
    {
        title: 'Night Bass', artist: 'Hallez Beats', bpm: 140, hue: 190, bars: 8,
        step(i, bar, t, sd) {
            const roots = [34, 34, 31, 29];
            const r = roots[bar % 4];
            const kicks = [0, 6, 10];
            if (kicks.includes(i)) { inst.kick(t, 0.95); inst.sub(t, r, sd * 3, 1); beatAt(t, 1); }
            if (i === 8) { inst.snare(t, 1); inst.clap(t, 0.7); beatAt(t, 0.6); }
            inst.hat(t, i % 2 ? 0.25 : 0.5);
            if (bar % 2 === 1 && (i === 14 || i === 15)) { inst.hat(t, 0.6); inst.hat(t + sd / 2, 0.4); }
            if (i === 3 || i === 11) inst.pluck(t, r + 36, 0.12, 0.9, 'sawtooth');
            if (i === 0 && bar % 4 === 0) inst.pad(t, [r + 24, r + 27, r + 31], sd * 32);
        }
    },
    {
        title: 'Sunset Pulse', artist: 'Hallez Beats', bpm: 104, hue: 18, bars: 8,
        step(i, bar, t, sd) {
            const roots = [40, 36, 43, 38];          // E C G D
            const r = roots[bar % 4];
            if (i === 0 || i === 7 || i === 10) { inst.kick(t, 0.95); beatAt(t, 1); }
            if (i === 4 || i === 12) { inst.snare(t, 0.9); beatAt(t, 0.5); }
            if (i % 2 === 0) inst.hat(t, 0.4);
            inst.bass(t, r + (i % 8 === 6 ? 7 : 0), sd * 1.1, 0.85);
            if (i % 4 === 0) inst.pluck(t, r + 28 + [0, 4, 7, 12][(i / 4) % 4], 0.3, 0.9, 'triangle');
            if (i === 0) inst.pad(t, [r + 12, r + 16, r + 19], sd * 16);
        }
    }
];

/* ---------- Плеер: состояние ---------- */
const player = {
    mode: 'synth',        // 'synth' | 'file'
    index: 0,
    playing: false,
    step: 0, bar: 0, nextTime: 0, timer: null,
    startTime: 0, pausedAt: 0,
    audioEl: null, mediaSrc: null, fileName: ''
};

let beatImpulse = 0;       // сюда пишут инструменты (для немедленной реакции)
function beat(power) { beatImpulse = Math.max(beatImpulse, power); }
function beatAt(t, power) {                 // t — время в часах AudioContext
    const ms = Math.max(0, (t - ctx.currentTime) * 1000 - 20);
    setTimeout(() => { if (player.playing) beat(power); }, ms);
}

const track = () => TRACKS[player.index];
const synthLoopLen = () => track().bars * 16 * (60 / track().bpm / 4);

function schedule() {
    const tr = track();
    const sd = 60 / tr.bpm / 4;
    while (player.nextTime < ctx.currentTime + 0.14) {
        tr.step(player.step, player.bar, player.nextTime, sd);
        player.nextTime += sd;
        player.step = (player.step + 1) % 16;
        if (player.step === 0) player.bar = (player.bar + 1) % tr.bars;
    }
}

function startSynth() {
    ensureAudio();
    if (!ctx) return;
    player.mode = 'synth';
    player.nextTime = ctx.currentTime + 0.06;
    player.startTime = ctx.currentTime - player.pausedAt;
    clearInterval(player.timer);
    player.timer = setInterval(schedule, 25);
}
function stopSynth() { clearInterval(player.timer); player.timer = null; }

function play() {
    ensureAudio();
    if (!ctx) return;
    if (player.mode === 'file' && player.audioEl) {
        player.audioEl.play();
    } else {
        // при возобновлении начинаем с начала такта — звучит чище
        player.step = 0;
        player.pausedAt = player.bar * 16 * (60 / track().bpm / 4);
        startSynth();
    }
    player.playing = true;
    syncUI();
}
function pause() {
    if (player.mode === 'file' && player.audioEl) player.audioEl.pause();
    else { stopSynth(); player.pausedAt = ctx ? (ctx.currentTime - player.startTime) % synthLoopLen() : 0; }
    player.playing = false;
    syncUI();
}
function toggle() { player.playing ? pause() : play(); }

function setTrack(i) {
    player.index = (i + TRACKS.length) % TRACKS.length;
    player.mode = 'synth';
    if (player.audioEl) { player.audioEl.pause(); }
    player.step = 0; player.bar = 0; player.pausedAt = 0;
    paletteTarget = track().hue;
    if (player.playing) { stopSynth(); startSynth(); }
    syncUI();
    flashPulse(1);
}
const next = () => setTrack(player.index + 1);
const prev = () => setTrack(player.index - 1);

/* ---------- Свой файл ---------- */
$('#fileInput').addEventListener('change', e => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    ensureAudio();
    if (!ctx) return;
    stopSynth();
    if (!player.audioEl) {
        player.audioEl = new Audio();
        player.audioEl.loop = true;
        player.audioEl.crossOrigin = 'anonymous';
        player.mediaSrc = ctx.createMediaElementSource(player.audioEl);
        player.mediaSrc.connect(master);
        player.audioEl.addEventListener('ended', () => { player.playing = false; syncUI(); });
    }
    if (player.audioEl.src) URL.revokeObjectURL(player.audioEl.src);
    player.audioEl.src = URL.createObjectURL(file);
    player.mode = 'file';
    player.fileName = file.name.replace(/\.[^.]+$/, '');
    player.audioEl.play().then(() => { player.playing = true; syncUI(); }).catch(() => { player.playing = false; syncUI(); });
    e.target.value = '';
});

/* ---------- UI плеера ---------- */
const ui = {
    dockTitle: $('#dockTitle'), dockArtist: $('#dockArtist'), dockPlay: $('#dockPlay'),
    dockTime: $('#dockTime'), dockTotal: $('#dockTotal'), dockBarFill: $('#dockBarFill'), dockBar: $('#dockBar'),
    pTitle: $('#previewTitle'), pArtist: $('#previewArtist'), pPlay: $('#previewPlay'), pStatus: $('#previewStatus'),
    pBpm: $('#previewBpm'), pFill: $('#previewProgressFill'), pBar: $('#previewProgress'),
    heroLabel: $('#heroPlayLabel'), mute: [$('#dockMute'), $('#previewMute')]
};

function syncUI() {
    const isFile = player.mode === 'file';
    const title = isFile ? player.fileName : track().title;
    const artist = isFile ? 'Твой трек' : track().artist;
    const bpm = isFile ? '' : ` · ${track().bpm} BPM`;
    ui.dockTitle.textContent = title;
    ui.dockArtist.textContent = artist + bpm;
    ui.pTitle.textContent = title;
    ui.pArtist.textContent = artist;
    ui.pBpm.textContent = isFile ? '— BPM' : `${track().bpm} BPM`;
    const icon = player.playing ? '⏸' : '▶';
    ui.dockPlay.textContent = icon;
    ui.pPlay.textContent = icon;
    ui.pStatus.textContent = player.playing ? 'Сейчас играет' : 'Нажми Play';
    ui.heroLabel.textContent = player.playing ? '⏸ Пауза' : '▶ Включить бит';
    document.body.classList.toggle('playing', player.playing);
    ui.mute.forEach(b => b.textContent = muted ? '🔇' : '🔊');
}

function updateProgress() {
    let cur = 0, total = 0;
    if (player.mode === 'file' && player.audioEl) {
        cur = player.audioEl.currentTime || 0; total = player.audioEl.duration || 0;
    } else if (ctx) {
        total = synthLoopLen();
        cur = player.playing ? (ctx.currentTime - player.startTime) % total : player.pausedAt;
    }
    const pct = total ? clamp(cur / total * 100, 0, 100) : 0;
    ui.dockBarFill.style.width = pct + '%';
    ui.pFill.style.width = pct + '%';
    ui.dockTime.textContent = fmt(cur);
    ui.dockTotal.textContent = total && isFinite(total) ? fmt(total) : '0:00';
}

function seekFromEvent(e, el) {
    const r = el.getBoundingClientRect();
    const p = clamp((e.clientX - r.left) / r.width, 0, 1);
    if (player.mode === 'file' && player.audioEl && isFinite(player.audioEl.duration)) {
        player.audioEl.currentTime = p * player.audioEl.duration;
    } else if (ctx) {
        // для синтеза — перемотка по тактам
        const tr = track();
        player.bar = Math.floor(p * tr.bars) % tr.bars;
        player.step = 0;
        player.pausedAt = player.bar * 16 * (60 / tr.bpm / 4);
        if (player.playing) { stopSynth(); startSynth(); }
    }
}
ui.dockBar.addEventListener('click', e => seekFromEvent(e, ui.dockBar));
ui.pBar.addEventListener('click', e => seekFromEvent(e, ui.pBar));

/* громкость / mute */
function applyVolume() { if (master) master.gain.setTargetAtTime(muted ? 0 : volume, ctx.currentTime, 0.02); }
$('#volume').addEventListener('input', e => { volume = e.target.value / 100; muted = false; if (ctx) applyVolume(); syncUI(); });
function toggleMute() { muted = !muted; if (ctx) applyVolume(); syncUI(); }
ui.mute.forEach(b => b.addEventListener('click', toggleMute));

/* кнопки */
['dockPlay', 'previewPlay', 'heroPlay'].forEach(id => $('#' + id).addEventListener('click', toggle));
['dockNext', 'previewNext'].forEach(id => $('#' + id).addEventListener('click', next));
['dockPrev', 'previewPrev'].forEach(id => $('#' + id).addEventListener('click', prev));

/* клавиатура: пробел — пауза, ←/→ — треки, Q W E R A S D F — пэды */
const PAD_KEYS = {};
$$('.pad').forEach(p => { PAD_KEYS[p.dataset.key.toLowerCase()] = p; });
document.addEventListener('keydown', e => {
    if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' && e.target.type !== 'range') return;
    if (e.code === 'Space' && tag !== 'button' && tag !== 'a') { e.preventDefault(); toggle(); return; }
    if (e.code === 'ArrowRight' && tag !== 'input') { next(); return; }
    if (e.code === 'ArrowLeft' && tag !== 'input') { prev(); return; }
    const pad = PAD_KEYS[e.key.toLowerCase()];
    if (pad) hitPad(pad);
});

/* ---------- Пэды ---------- */
function hitPad(el) {
    ensureAudio();
    if (!ctx) return;
    const t = ctx.currentTime + 0.005;
    switch (el.dataset.pad) {
        case 'kick':  inst.kick(t); break;
        case 'snare': inst.snare(t); break;
        case 'hat':   inst.hat(t, 1.2, true); break;
        case 'clap':  inst.clap(t); break;
        case 'bass':  inst.sub(t, 33, 0.7); break;
        case 'tom':   inst.tom(t); break;
        case 'zap':   inst.zap(t); break;
        case 'chord': inst.pad(t, [57, 60, 64, 67], 1.4); inst.pluck(t, 81, 0.3); break;
    }
    beat(el.dataset.pad === 'hat' ? 0.35 : 0.9);
    // цвет пада красит весь сайт на секунду
    const c = getComputedStyle(el).getPropertyValue('--pad-c');
    const m = /hsl\(\s*(\d+)/.exec(c);
    if (m) { paletteTarget = +m[1]; paletteHold = performance.now() + 900; }
    el.classList.add('hit');
    setTimeout(() => el.classList.remove('hit'), 120);
    spawnBurst(0.6);
}
$$('.pad').forEach(p => {
    p.addEventListener('pointerdown', e => { e.preventDefault(); hitPad(p); });
});

/* ============================================================
   Визуализация
   ============================================================ */
const bg = $('#bgCanvas'), bgx = bg.getContext('2d');
const vz = $('#visualizerCanvas'), vzx = vz.getContext('2d');
const miniBars = $$('#miniEq i');

let W = 0, H = 0, DPR = 1;
function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    W = window.innerWidth; H = window.innerHeight;
    bg.width = W * DPR; bg.height = H * DPR;
    bgx.setTransform(DPR, 0, 0, DPR, 0, 0);
    const r = vz.getBoundingClientRect();
    vz.width = Math.max(1, r.width * DPR); vz.height = Math.max(1, r.height * DPR);
    vzx.setTransform(DPR, 0, 0, DPR, 0, 0);
}
window.addEventListener('resize', resize);
resize();

const MODES = ['Кольца', 'Бары', 'Волна', 'Частицы'];
let modeIdx = 0;
$('#vizModeBtn').addEventListener('click', () => {
    modeIdx = (modeIdx + 1) % MODES.length;
    $('#vizModeBtn').textContent = 'Режим фона: ' + MODES[modeIdx];
});
$('#softBtn').addEventListener('click', () => {
    soft = !soft;
    try { localStorage.setItem('hallez-soft', soft ? '1' : '0'); } catch (_) {}
    renderSoft();
});
function renderSoft() {
    const b = $('#softBtn');
    b.textContent = 'Мягкий режим: ' + (soft ? 'вкл' : 'выкл');
    b.setAttribute('aria-pressed', soft);
}
renderSoft();
$('#zenBtn').addEventListener('click', () => {
    const on = document.body.classList.toggle('zen');
    $('#zenBtn').textContent = on ? 'Вернуть сайт' : 'Только визуал';
    window.scrollTo({ top: 0 });
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && document.body.classList.contains('zen')) { $('#zenBtn').click(); }
});

/* ---------- Состояние анимации ---------- */
let hue = track().hue, hueBase = hue, hueKick = 0, paletteTarget = hue, paletteHold = 0;
let bass = 0, mid = 0, high = 0, level = 0;
let pulse = 0, lastBeat = 0;
let bassAvg = 0.2, prevBass = 0;
let rings = [], particles = [];
let rot = 0, dtScale = 1, lastFrame = performance.now();

function flashPulse(v) { beat(v); }

function spawnBurst(power) {
    const n = Math.round(14 * power);
    for (let i = 0; i < n; i++) {
        const a = Math.random() * Math.PI * 2, sp = (2 + Math.random() * 6) * (0.6 + power);
        particles.push({ x: W / 2, y: H * 0.45, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp, life: 1, size: 1.5 + Math.random() * 3, h: hue + Math.random() * 60 - 20 });
    }
    if (particles.length > 260) particles.splice(0, particles.length - 260);
}

function analyse(now) {
    let b = 0, m = 0, h = 0;
    if (analyser && (player.playing || beatImpulse > 0 || ctxActive())) {
        analyser.getByteFrequencyData(freq);
        analyser.getByteTimeDomainData(wave);
        const n = freq.length;                      // 512 бинов
        const avg = (a, z) => { let s = 0; for (let i = a; i < z; i++) s += freq[i]; return s / ((z - a) * 255); };
        b = avg(1, 7); m = avg(7, 60); h = avg(60, 200);
    }
    // плавное сглаживание
    bass += (b - bass) * 0.5;
    mid  += (m - mid)  * 0.4;
    high += (h - high) * 0.4;
    level = (bass * 1.4 + mid + high * 0.6) / 3;

    // детекция бита по басу (для загруженных треков и пэдов тоже)
    bassAvg += (b - bassAvg) * 0.04;
    const rising = b - prevBass > 0.03;
    prevBass = b;
    if (player.mode === 'file' && player.playing && rising && b > 0.38 && b > bassAvg * 1.2 && now - lastBeat > 260) {
        beat(Math.min(1, 0.5 + b * 0.6));
    }
    if (beatImpulse > 0.01) {
        const v = beatImpulse * (soft ? 0.45 : 1);
        if (now - lastBeat > 90) {
            pulse = Math.max(pulse, v);
            lastBeat = now;
            rings.push({ r: 30, a: 0.9 * v + 0.1, w: 3 + v * 5, h: hue });
            if (rings.length > 8) rings.shift();
            if (!soft) hueKick += 16 * v;           // цвет «перескакивает» на ударе
            spawnBurst(v);
        }
        beatImpulse = 0;
    }
    pulse *= Math.pow(0.9, dtScale);
}
function ctxActive() { return ctx && ctx.state === 'running'; }

function updateCSS() {
    // база плавно тянется к цвету трека (к цвету пада — быстрее)
    const k = performance.now() > paletteHold ? 0.03 : 0.14;
    const d = ((paletteTarget - hueBase + 540) % 360) - 180;
    hueBase += d * k;
    if (!player.playing) hueBase += 0.08;            // в тишине цвет медленно дрейфует
    hueBase = ((hueBase % 360) + 360) % 360;
    hueKick *= Math.pow(0.93, dtScale);
    hue = ((hueBase + hueKick) % 360 + 360) % 360;
    const s = root.style;
    s.setProperty('--hue', hue.toFixed(1));
    s.setProperty('--bass', bass.toFixed(3));
    s.setProperty('--mid', mid.toFixed(3));
    s.setProperty('--pulse', (pulse * (soft ? 0.7 : 1)).toFixed(3));
}

/* ---------- Рисование фона ---------- */
function drawBackground(now) {
    const cx = W / 2, cy = H * 0.45;
    const idle = !player.playing && pulse < 0.05;
    const breathe = idle ? (Math.sin(now / 1400) * 0.5 + 0.5) * 0.18 : 0;
    const e = clamp(level * 1.6 + pulse * 0.5 + breathe, 0, 1.2);

    // база + «заливка» цветом на бите
    bgx.globalCompositeOperation = 'source-over';
    bgx.fillStyle = `rgba(7, 6, 13, ${soft ? 0.35 : 0.22})`;
    bgx.fillRect(0, 0, W, H);

    const g = bgx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(W, H) * (0.55 + e * 0.25));
    g.addColorStop(0, `hsla(${hue}, 95%, ${18 + e * 24}%, ${0.55 + pulse * 0.3})`);
    g.addColorStop(0.5, `hsla(${hue + 50}, 90%, ${10 + e * 10}%, 0.35)`);
    g.addColorStop(1, 'rgba(7, 6, 13, 0)');
    bgx.fillStyle = g;
    bgx.fillRect(0, 0, W, H);

    bgx.globalCompositeOperation = 'lighter';
    const mode = MODES[modeIdx];
    if (mode === 'Кольца') drawRings(bgx, cx, cy, W, H, e);
    if (mode === 'Бары') drawBars(bgx, W, H, e, true);
    if (mode === 'Волна') drawWave(bgx, W, H, e);
    if (mode === 'Частицы') { /* только частицы */ }
    drawParticles(bgx);
    bgx.globalCompositeOperation = 'source-over';
}

function drawRings(c, cx, cy, w, h, e) {
    const base = Math.min(w, h) * 0.16 * (1 + bass * 0.5 + pulse * 0.25);
    const n = 96;
    rot += 0.002 + mid * 0.01;
    c.lineCap = 'round';
    for (let i = 0; i < n; i++) {
        const a = (i / n) * Math.PI * 2 + rot;
        const idx = Math.floor((i < n / 2 ? i : n - i) / (n / 2) * 110) + 2;
        const v = analyser && (player.playing || ctxActive()) ? freq[idx] / 255 : (Math.sin(now_() / 600 + i * 0.3) * 0.5 + 0.5) * 0.12;
        const len = 6 + v * Math.min(w, h) * 0.22;
        c.strokeStyle = `hsla(${hue + i * 2.2}, 100%, ${55 + v * 25}%, ${0.35 + v * 0.6})`;
        c.lineWidth = 2.5;
        c.beginPath();
        c.moveTo(cx + Math.cos(a) * base, cy + Math.sin(a) * base);
        c.lineTo(cx + Math.cos(a) * (base + len), cy + Math.sin(a) * (base + len));
        c.stroke();
    }
    // расходящиеся кольца на бите
    for (let i = rings.length - 1; i >= 0; i--) {
        const r = rings[i];
        r.r += 7 + pulse * 6; r.a *= 0.955;
        if (r.a < 0.02 || r.r > Math.max(w, h)) { rings.splice(i, 1); continue; }
        c.strokeStyle = `hsla(${r.h + 40}, 100%, 65%, ${r.a})`;
        c.lineWidth = r.w;
        c.beginPath(); c.arc(cx, cy, base + r.r, 0, Math.PI * 2); c.stroke();
    }
}
const now_ = () => performance.now();

function drawBars(c, w, h, e, full) {
    const n = full ? 64 : 48;
    const bw = w / n;
    for (let i = 0; i < n; i++) {
        const idx = Math.floor(Math.pow(i / n, 1.6) * 220) + 1;
        const v = analyser && (player.playing || ctxActive()) ? freq[idx] / 255 : (Math.sin(now_() / 500 + i * 0.4) * 0.5 + 0.5) * 0.1;
        const bh = (8 + v * h * (full ? 0.5 : 0.8));
        c.fillStyle = `hsla(${hue + i * 3}, 100%, ${50 + v * 25}%, ${0.25 + v * 0.55})`;
        c.fillRect(i * bw + 2, h - bh, bw - 4, bh);                 // снизу
        if (full) c.fillRect(i * bw + 2, 0, bw - 4, bh * 0.45);     // и сверху
    }
}

function drawWave(c, w, h, e) {
    const lines = 3;
    for (let k = 0; k < lines; k++) {
        c.beginPath();
        const amp = (h * 0.14) * (1 + e * 1.4) * (1 - k * 0.22);
        for (let x = 0; x <= w; x += 6) {
            let v = 0;
            if (analyser && (player.playing || ctxActive())) {
                const i = Math.floor(x / w * (wave.length - 1));
                v = (wave[i] - 128) / 128;
            } else v = Math.sin(x / 90 + now_() / 700 + k) * 0.08;
            const y = h * (0.45 + k * 0.08) + v * amp + Math.sin(x / 140 + now_() / 900 + k * 2) * 12 * (0.4 + e);
            x === 0 ? c.moveTo(x, y) : c.lineTo(x, y);
        }
        c.strokeStyle = `hsla(${hue + k * 55}, 100%, 62%, ${0.6 - k * 0.15})`;
        c.lineWidth = 3 - k * 0.6;
        c.stroke();
    }
}

function drawParticles(c) {
    for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        p.x += p.vx; p.y += p.vy; p.vx *= 0.985; p.vy *= 0.985; p.vy += 0.03; p.life -= 0.014;
        if (p.life <= 0) { particles.splice(i, 1); continue; }
        c.fillStyle = `hsla(${p.h}, 100%, 65%, ${p.life * 0.9})`;
        c.beginPath(); c.arc(p.x, p.y, p.size * (0.5 + p.life), 0, Math.PI * 2); c.fill();
    }
}

/* ---------- Визуализатор в секции + мини-эквалайзер ---------- */
function drawSection() {
    const r = vz.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight) return;   // не рисуем вне экрана
    const w = r.width, h = r.height;
    vzx.clearRect(0, 0, w, h);
    const n = 72, bw = w / n;
    const live = analyser && (player.playing || ctxActive());
    for (let i = 0; i < n; i++) {
        const idx = Math.floor(Math.pow(i / n, 1.5) * 230) + 1;
        const v = live ? freq[idx] / 255 : (Math.sin(now_() / 450 + i * 0.35) * 0.5 + 0.5) * 0.08;
        const bh = 4 + v * (h * 0.55);
        vzx.fillStyle = `hsl(${hue + i * 2.5}, 95%, ${52 + v * 20}%)`;
        vzx.shadowColor = `hsl(${hue + i * 2.5}, 100%, 60%)`;
        vzx.shadowBlur = 8 + v * 14;
        // зеркально от центральной линии
        vzx.fillRect(i * bw + 1, h * 0.5 - bh, bw - 3, bh);
        vzx.globalAlpha = 0.35;
        vzx.fillRect(i * bw + 1, h * 0.5, bw - 3, bh * 0.7);
        vzx.globalAlpha = 1;
    }
    vzx.shadowBlur = 0;
    // осциллограмма сверху
    if (live) {
        vzx.beginPath();
        for (let x = 0; x < w; x += 3) {
            const v = (wave[Math.floor(x / w * (wave.length - 1))] - 128) / 128;
            const y = h * 0.14 + v * h * 0.1;
            x === 0 ? vzx.moveTo(x, y) : vzx.lineTo(x, y);
        }
        vzx.strokeStyle = `hsla(${hue + 40}, 100%, 70%, .8)`;
        vzx.lineWidth = 2; vzx.stroke();
    }
}
function drawMiniEq() {
    const n = miniBars.length;
    const live = analyser && (player.playing || ctxActive());
    for (let i = 0; i < n; i++) {
        const idx = Math.floor(Math.pow(i / n, 1.4) * 160) + 1;
        const v = live ? freq[idx] / 255 : 0.05 + (Math.sin(now_() / 500 + i) * 0.5 + 0.5) * 0.04;
        miniBars[i].style.height = (8 + v * 92) + '%';
    }
}

/* ---------- Главный цикл ---------- */
let frame = 0;
function loop(now) {
    dtScale = clamp((now - lastFrame) / 16.667, 0.5, 4);   // 1 = кадр при 60 Гц
    lastFrame = now;
    analyse(now);
    updateCSS();
    drawBackground(now);
    if (frame % 2 === 0) { drawSection(); drawMiniEq(); }
    if (frame % 6 === 0) updateProgress();
    frame++;
    requestAnimationFrame(loop);
}
// первый кадр — залить фон
bgx.fillStyle = '#07060d'; bgx.fillRect(0, 0, W, H);
requestAnimationFrame(loop);
syncUI();

/* ============================================================
   Остальная «жизнь» сайта
   ============================================================ */

// плавный скролл
$$('a[href^="#"]').forEach(a => a.addEventListener('click', e => {
    const id = a.getAttribute('href');
    if (id.length < 2) return;
    const target = $(id);
    if (target) { e.preventDefault(); target.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
}));

// навбар
const navbar = $('.navbar');
window.addEventListener('scroll', () => navbar.classList.toggle('scrolled', window.scrollY > 50), { passive: true });

// появление блоков
const io = new IntersectionObserver(entries => entries.forEach(en => {
    if (en.isIntersecting) { en.target.style.opacity = '1'; en.target.style.translate = '0 0'; io.unobserve(en.target); }
}), { threshold: 0.1, rootMargin: '0px 0px -40px 0px' });
$$('.feature-card, .command-item, .step, .pad').forEach((el, i) => {
    el.style.opacity = '0'; el.style.translate = '0 28px';
    el.style.transition = `opacity .6s ease ${(i % 6) * 0.05}s, translate .6s ease ${(i % 6) * 0.05}s, transform .25s ease, border-color .3s, box-shadow .3s, background .3s`;
    io.observe(el);
});

// рипл на кнопках
document.addEventListener('click', e => {
    const b = e.target.closest('.btn, .control-btn, .chip, .dock-btn');
    if (!b) return;
    const r = b.getBoundingClientRect(), size = Math.max(r.width, r.height);
    const s = document.createElement('span');
    s.className = 'ripple';
    s.style.cssText = `width:${size}px;height:${size}px;left:${e.clientX - r.left - size / 2}px;top:${e.clientY - r.top - size / 2}px`;
    if (getComputedStyle(b).position === 'static') b.style.position = 'relative';
    b.style.overflow = 'hidden';
    b.appendChild(s);
    setTimeout(() => s.remove(), 600);
});

// «магнитные» кнопки и подсветка карточек за курсором
const fine = window.matchMedia('(pointer: fine)').matches;
if (fine) {
    $$('.btn-large').forEach(b => {
        b.addEventListener('pointermove', e => {
            const r = b.getBoundingClientRect();
            b.style.translate = `${(e.clientX - r.left - r.width / 2) * 0.12}px ${(e.clientY - r.top - r.height / 2) * 0.2}px`;
        });
        b.addEventListener('pointerleave', () => { b.style.translate = '0 0'; });
    });
    $$('.feature-card').forEach(card => {
        card.addEventListener('pointermove', e => {
            const r = card.getBoundingClientRect();
            const x = e.clientX - r.left, y = e.clientY - r.top;
            card.style.setProperty('--mx', x + 'px'); card.style.setProperty('--my', y + 'px');
            const rx = (y - r.height / 2) / 18, ry = (r.width / 2 - x) / 18;
            card.style.transform = `perspective(900px) rotateX(${rx}deg) rotateY(${ry}deg) translateY(-4px)`;
        });
        card.addEventListener('pointerleave', () => { card.style.transform = ''; });
    });
}

// печатающийся подзаголовок
const sub = $('#heroSubtitle');
if (sub && !reduceMotion) {
    const text = sub.textContent.trim().replace(/\s+/g, ' ');
    sub.textContent = '';
    let i = 0;
    (function type() { if (i < text.length) { sub.textContent += text[i++]; setTimeout(type, 22); } })();
}

// пауза тяжёлых вещей, когда вкладка скрыта
document.addEventListener('visibilitychange', () => {
    if (document.hidden && player.playing && player.mode === 'synth') { /* музыка продолжает играть */ }
});

console.log('%c🎵 HALLEZ MUSIC', 'color:#b36bff;font-size:28px;font-weight:800;');
})();
