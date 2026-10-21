// 홈 · 카테고리 상세의 필터 칩과 정렬. 항목의 data-* 속성으로 판정한다.
(() => {
  const group = document.querySelector('[data-filter-group]');
  const target = document.querySelector('[data-filter-target]');
  if (!group || !target) return;
  const empty = document.querySelector('[data-filter-empty]');
  const items = Array.from(target.children);

  const match = (el, f) => {
    if (f === 'all') return true;
    if (f === 'top') return Number(el.dataset.rating) >= 4;
    if (f === 'long') return el.dataset.life === '긺';
    if (f === 'short') return el.dataset.short === '1';
    if (f.startsWith('life:')) return el.dataset.life === f.slice(5);
    return true;
  };

  group.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-filter]');
    if (!btn) return;
    group.querySelectorAll('[data-filter]').forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
    let shown = 0;
    items.forEach((el) => { const ok = !el.dataset.removed && match(el, btn.dataset.filter); el.hidden = !ok; shown += ok; });
    target.hidden = shown === 0;  // 빈 목록의 테두리가 남지 않게 한다
    if (empty) empty.hidden = shown > 0;
  });

  const sort = document.querySelector('[data-sort]');
  const applySort = () => {
    const key = {
      date: (el) => el.dataset.date,
      rating: (el) => `${el.dataset.rating}|${el.dataset.date}`,
      life: (el) => `${el.dataset.liferank}|${el.dataset.date}`,
    }[sort.value];
    items.slice().sort((a, b) => key(b).localeCompare(key(a))).forEach((el) => target.appendChild(el));
  };
  if (sort) {
    sort.addEventListener('change', applySort);
    // 뒤로 가기로 돌아오면 브라우저가 select 값만 복원한다. 목록 순서도 그 값에 맞춘다
    window.addEventListener('pageshow', () => { if (sort.value !== 'date') applySort(); });
  }
})();
