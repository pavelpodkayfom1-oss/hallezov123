// Smooth scrolling for navigation links
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        e.preventDefault();
        const target = document.querySelector(this.getAttribute('href'));
        if (target) {
            target.scrollIntoView({
                behavior: 'smooth',
                block: 'start'
            });
        }
    });
});

// Navbar background on scroll
const navbar = document.querySelector('.navbar');
let lastScroll = 0;

window.addEventListener('scroll', () => {
    const currentScroll = window.pageYOffset;

    if (currentScroll > 50) {
        navbar.style.background = 'rgba(0, 0, 0, 0.98)';
        navbar.style.boxShadow = '0 4px 20px rgba(255, 255, 255, 0.1)';
    } else {
        navbar.style.background = 'rgba(0, 0, 0, 0.9)';
        navbar.style.boxShadow = 'none';
    }

    lastScroll = currentScroll;
});

// Animate elements on scroll
const observerOptions = {
    threshold: 0.1,
    rootMargin: '0px 0px -50px 0px'
};

const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.style.opacity = '1';
            entry.target.style.transform = 'translateY(0)';
        }
    });
}, observerOptions);

// Observe all feature cards, command items, and steps
document.querySelectorAll('.feature-card, .command-item, .step').forEach(el => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(30px)';
    el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
    observer.observe(el);
});

// Music player preview animation
const playBtn = document.querySelector('.play-btn');
const progressFill = document.querySelector('.progress-fill');

if (playBtn) {
    let isPlaying = true;
    playBtn.addEventListener('click', () => {
        isPlaying = !isPlaying;
        playBtn.textContent = isPlaying ? '▶️' : '⏸️';

        if (isPlaying) {
            progressFill.style.animationPlayState = 'running';
        } else {
            progressFill.style.animationPlayState = 'paused';
        }
    });
}

// Audio Visualizer Animation
const bars = document.querySelectorAll('.bar');

function animateBars() {
    bars.forEach((bar, index) => {
        const randomHeight = Math.random() * 80 + 20;
        const delay = index * 50;

        setTimeout(() => {
            bar.style.height = randomHeight + '%';
        }, delay);
    });
}

// Animate bars every 500ms
if (bars.length > 0) {
    setInterval(animateBars, 500);
}

// Waveform animation
const wavePath = document.getElementById('wavePath');

if (wavePath) {
    let phase = 0;

    function animateWave() {
        phase += 0.05;

        const points = [];
        for (let x = 0; x <= 1000; x += 20) {
            const y = 100 + Math.sin((x / 100) + phase) * 30 + Math.sin((x / 50) + phase * 2) * 15;
            points.push(`${x},${y}`);
        }

        const pathData = `M${points.join(' L')}`;
        wavePath.setAttribute('d', pathData);

        requestAnimationFrame(animateWave);
    }

    animateWave();
}

// Add ripple effect to buttons
document.querySelectorAll('.btn, .control-btn').forEach(button => {
    button.addEventListener('click', function(e) {
        const ripple = document.createElement('span');
        const rect = this.getBoundingClientRect();
        const size = Math.max(rect.width, rect.height);
        const x = e.clientX - rect.left - size / 2;
        const y = e.clientY - rect.top - size / 2;

        ripple.style.width = ripple.style.height = size + 'px';
        ripple.style.left = x + 'px';
        ripple.style.top = y + 'px';
        ripple.classList.add('ripple');

        this.appendChild(ripple);

        setTimeout(() => ripple.remove(), 600);
    });
});

// Add ripple styles dynamically
const style = document.createElement('style');
style.textContent = `
    .btn, .control-btn {
        position: relative;
        overflow: hidden;
    }

    .ripple {
        position: absolute;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.4);
        transform: scale(0);
        animation: ripple-animation 0.6s ease-out;
        pointer-events: none;
    }

    @keyframes ripple-animation {
        to {
            transform: scale(4);
            opacity: 0;
        }
    }
`;
document.head.appendChild(style);

// Glitch effect on title
const heroTitle = document.querySelector('.hero-title');

if (heroTitle) {
    let glitchInterval;

    function glitchEffect() {
        heroTitle.style.textShadow = `
            ${Math.random() * 5 - 2}px ${Math.random() * 5 - 2}px 0 rgba(255, 255, 255, 0.7),
            ${Math.random() * 5 - 2}px ${Math.random() * 5 - 2}px 0 rgba(100, 100, 100, 0.7)
        `;

        setTimeout(() => {
            heroTitle.style.textShadow = 'none';
        }, 50);
    }

    // Random glitch effect every 3-7 seconds
    function scheduleGlitch() {
        const delay = Math.random() * 4000 + 3000;
        setTimeout(() => {
            glitchEffect();
            scheduleGlitch();
        }, delay);
    }

    scheduleGlitch();
}

// Parallax effect on scroll
window.addEventListener('scroll', () => {
    const scrolled = window.pageYOffset;
    const parallaxElements = document.querySelectorAll('.music-player-preview');

    parallaxElements.forEach(el => {
        const speed = 0.3;
        el.style.transform = `translateY(${scrolled * speed}px)`;
    });
});

// Mouse trail effect
let mouseTrail = [];
const trailLength = 20;

document.addEventListener('mousemove', (e) => {
    mouseTrail.push({ x: e.clientX, y: e.clientY, time: Date.now() });

    if (mouseTrail.length > trailLength) {
        mouseTrail.shift();
    }
});

// Particle system for hero section
const hero = document.querySelector('.hero');

if (hero && window.innerWidth > 768) {
    const particleCount = 30;

    for (let i = 0; i < particleCount; i++) {
        const particle = document.createElement('div');
        particle.className = 'particle';

        const size = Math.random() * 3 + 1;
        const startX = Math.random() * 100;
        const startY = Math.random() * 100;
        const duration = Math.random() * 20 + 10;
        const delay = Math.random() * 5;

        particle.style.cssText = `
            position: absolute;
            width: ${size}px;
            height: ${size}px;
            background: rgba(255, 255, 255, ${Math.random() * 0.5 + 0.2});
            border-radius: 50%;
            left: ${startX}%;
            top: ${startY}%;
            pointer-events: none;
            animation: float-particle ${duration}s ease-in-out infinite;
            animation-delay: ${delay}s;
            box-shadow: 0 0 ${size * 2}px rgba(255, 255, 255, 0.5);
        `;

        hero.appendChild(particle);
    }

    const particleStyle = document.createElement('style');
    particleStyle.textContent = `
        @keyframes float-particle {
            0%, 100% {
                transform: translate(0, 0);
                opacity: 0;
            }
            10%, 90% {
                opacity: 1;
            }
            25% {
                transform: translate(${Math.random() * 100 - 50}px, ${Math.random() * 100 - 50}px);
            }
            50% {
                transform: translate(${Math.random() * 100 - 50}px, ${Math.random() * 100 - 50}px);
            }
            75% {
                transform: translate(${Math.random() * 100 - 50}px, ${Math.random() * 100 - 50}px);
            }
        }
    `;
    document.head.appendChild(particleStyle);
}

// Typing effect for hero subtitle
const subtitle = document.querySelector('.hero-subtitle');

if (subtitle) {
    const text = subtitle.textContent;
    subtitle.textContent = '';
    let charIndex = 0;

    function typeText() {
        if (charIndex < text.length) {
            subtitle.textContent += text.charAt(charIndex);
            charIndex++;
            setTimeout(typeText, 30);
        }
    }

    // Start typing after a short delay
    setTimeout(typeText, 500);
}

// Feature cards hover effect - 3D tilt
document.querySelectorAll('.feature-card').forEach(card => {
    card.addEventListener('mousemove', (e) => {
        const rect = card.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        const centerX = rect.width / 2;
        const centerY = rect.height / 2;

        const rotateX = (y - centerY) / 10;
        const rotateY = (centerX - x) / 10;

        card.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) translateY(-5px)`;
    });

    card.addEventListener('mouseleave', () => {
        card.style.transform = 'perspective(1000px) rotateX(0) rotateY(0) translateY(0)';
    });
});

// Console message
console.log('%c🎵 HALLEZ MUSIC BOT', 'color: #ffffff; font-size: 32px; font-weight: bold; text-shadow: 0 0 10px #ffffff;');
console.log('%c━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━', 'color: #666666; font-size: 14px;');
console.log('%cПриватный музыкальный бот', 'color: #999999; font-size: 16px;');
console.log('%cСайт загружен успешно!', 'color: #ffffff; font-size: 14px;');
console.log('%c━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━', 'color: #666666; font-size: 14px;');
