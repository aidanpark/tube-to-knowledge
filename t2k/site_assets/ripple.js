// Material Design 의 물결(ripple) 누름 표시. 누른 지점에서 원이 퍼지며, 요소 모양(둥근 모서리) 안에서만 보인다.
(() => {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const TARGETS = '.rm-btn, .chip, .pg, .hero .btn, .banner a, .vcard, .result, .rows > a, .prevnext a, .ctile, .ccard, .suggest button, .clear';
  document.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    const el = e.target.closest(TARGETS);
    if (!el || el.disabled) return;
    const r = el.getBoundingClientRect();
    const d = Math.hypot(r.width, r.height) * 2;
    const s = document.createElement('span');
    s.className = 'ripple';
    s.style.cssText = `width:${d}px;height:${d}px;left:${e.clientX - r.left - d / 2}px;top:${e.clientY - r.top - d / 2}px`;
    el.appendChild(s);
    const end = () => { s.classList.add('out'); setTimeout(() => s.remove(), 350); };
    ['pointerup', 'pointercancel', 'pointerleave'].forEach((t) => el.addEventListener(t, end, { once: true }));
  });
})();
