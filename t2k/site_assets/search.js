// 브라우저 안 검색. 사이트를 생성할 때 만든 search.json 을 읽는다 (외부 전송 없음).
(() => {
  const $ = (s) => document.querySelector(s);
  const input = $('#q'), clearBtn = $('.clear'), box = $('#suggest');
  const results = $('#results'), count = $('#count'), order = $('#order');
  const KIND = { '태그': ['#D2E3FC', '#174EA6', 0], '카테고리': ['#CEEAD6', '#0D652D', 1], '채널': ['#EFF7CF', '#3C4A0B', 2], '제목': ['#F1F3F4', '#3C4043', 3] };
  const state = { type: 'all', top: false, shorts: false, life: '', page: 1 };
  const LIFE_CLASS = { '짧음': 'c-amber', '중간': 'c-gray', '긺': 'c-sky' };
  const PAGE_SIZE = 20;
  // 화면 문구: base.html 이 넘긴 window.YT_I18N (locales/<언어>/site.toml 의 [js] · 수명 · 종류 이름). {이름} 을 vars 로 채운다
  const tr = (k, vars = {}) => (window.YT_I18N?.[k] ?? k).replace(/\{(\w+)\}/g, (_, n) => vars[n]);
  const pager = $('#pager');
  let items = [], pool = [], cursor = -1;

  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const norm = (s) => s.toLowerCase();
  // 검색어와 일치하는 부분을 tag 로 감싼다 (HTML 이스케이프 후)
  const hl = (text, q, tag) => {
    if (!q) return esc(text);
    const low = norm(text); let out = '', i = 0;
    for (;;) {
      const j = low.indexOf(q, i);
      if (j < 0) return out + esc(text.slice(i));
      out += esc(text.slice(i, j)) + `<${tag}>${esc(text.slice(j, j + q.length))}</${tag}>`;
      i = j + q.length;
    }
  };
  const ell = (s) => /[.!?。…]$/.test(s) ? s : s + '…';
  const snippet = (text, q) => {
    const j = q ? norm(text).indexOf(q) : -1;
    const cut = (t, n) => { const sp = t.lastIndexOf(' ', n); return t.slice(0, sp > n - 20 ? sp : n); };  // 낱말 중간에서 자르지 않는다
    if (j < 0) return text.length > 90 ? ell(cut(text, 90)) : text;
    let s = Math.max(0, j - 30);
    if (s > 0) { const sp = text.lastIndexOf(' ', s); s = sp >= 0 && j - sp < 60 ? sp + 1 : s; }  // 낱말 중간에서 시작하지 않는다
    let e = Math.min(text.length, j + q.length + 50);
    if (e < text.length) { const sp = text.indexOf(' ', e); e = sp >= 0 && sp - e < 15 ? sp : e; }
    const body = text.slice(s, e);
    return (s > 0 ? '…' : '') + (e < text.length ? ell(body) : body);
  };

  const buildPool = () => {
    const seen = new Set();
    const add = (kind, label, value) => { const k = kind + label; if (!seen.has(k)) { seen.add(k); pool.push({ kind, label, value }); } };
    items.forEach((it) => {
      it.tags.forEach((t) => add('태그', '#' + t, t));
      add('카테고리', it.category, it.category);
      if (it.type === '요약') add('채널', it.channel, it.channel);
      add('제목', it.title, it.title);
    });
  };

  const closeBox = () => { box.hidden = true; cursor = -1; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); };
  const suggest = (q) => {
    cursor = -1;
    input.removeAttribute('aria-activedescendant');
    if (!q) { closeBox(); return; }
    const starts = (p) => norm(p.label.replace('#', '')).startsWith(q) ? 0 : 1;
    const list = pool.filter((p) => norm(p.label).includes(q))
      .sort((a, b) => starts(a) - starts(b) || KIND[a.kind][2] - KIND[b.kind][2] || a.label.length - b.label.length)
      .slice(0, 6);
    box.innerHTML = list.map((p, i) =>
      `<button type="button" role="option" id="sg-${i}" aria-selected="false" tabindex="-1" data-i="${i}"><span class="kind" style="background:${KIND[p.kind][0]};color:${KIND[p.kind][1]}">${esc(tr('kind.' + p.kind))}</span><span class="lbl">${hl(p.label, q, 'b')}</span></button>`).join('');
    box._list = list;
    box.hidden = list.length === 0;
    input.setAttribute('aria-expanded', String(!box.hidden));
  };

  const render = () => {
    const raw = input.value.trim().normalize('NFC'), q = norm(raw);
    clearBtn.hidden = !raw;
    const hits = items.filter((it) => {
      if (window.removedSet && window.removedSet.has(it.id)) return false;  // 삭제 대기
      if (state.type !== 'all' && it.type !== state.type) return false;
      if (state.top && !(it.rating >= 4)) return false;
      if (state.shorts && !it.short) return false;
      if (state.life && it.life !== state.life) return false;
      if (!q) return true;
      const qs = q.replace(/^#/, ''), qc = qs.replace(/\s+/g, '');
      return [it.title, it.channel, it.category, it.text, it.tags.join(' '), it.id || ''].some((f) => norm(f).includes(qs))
        || (qc && it.tags.some((t) => norm(t).replace(/\s+/g, '').includes(qc)));
    });
    // 제목 · 태그 일치를 앞에 둔다. 나머지는 최신순
    const score = (it) => !q ? 0 : (norm(it.title).includes(q) ? 2 : 0) + (it.tags.some((t) => norm(t).includes(q)) ? 1 : 0);
    hits.sort((a, b) => score(b) - score(a) || b.sort.localeCompare(a.sort));
    order.textContent = tr(q ? 'order_relevance' : 'order_newest');
    const pages = Math.max(1, Math.ceil(hits.length / PAGE_SIZE));
    state.page = Math.min(Math.max(1, state.page), pages);
    const from = (state.page - 1) * PAGE_SIZE, shown = hits.slice(from, from + PAGE_SIZE);
    count.textContent = (raw ? tr('results', { q: raw, n: hits.length }) : tr('total', { n: hits.length }))
      + (pages > 1 ? ` · ${from + 1}–${from + shown.length}` : '');
    renderPager(pages);
    results.innerHTML = hits.length ? shown.map((it) => `
      <a class="result" href="${esc(it.href)}">
        <span class="pills">
          ${it.type !== '요약' ? `<span class="pill c-brand">${esc(tr('type.' + it.type))}</span>` : ''}
          ${it.short ? '<span class="pill c-brand">Shorts</span>' : ''}
          <span class="pill c-gray">${esc(it.category)}</span>
          ${it.rating ? `<span class="pill ${it.rating >= 4 ? 'c-lime' : 'c-gray'}">★ ${it.rating}</span>` : ''}
          ${it.life ? `<span class="pill ${LIFE_CLASS[it.life] || 'c-gray'}">${esc(tr('life.' + it.life))}</span>` : ''}
          <span class="mono" style="padding:2px 0;color:var(--muted)">${esc(it.date)}</span>
        </span>
        <span class="title">${hl(it.title, q, 'mark')}</span>
        <span class="snip">${hl(snippet(it.text, q), q, 'mark')}</span>
        <span class="ch">${esc(it.channel)}${it.type === '요약' ? ` · <span class="mono">${esc(it.id)}</span>` : ''}</span>
      </a>`).join('')
      : `<div class="empty" style="margin:0"><b>${esc(tr('no_results'))}</b>${esc(tr('no_results_body'))}</div>`;
  };

  // 쪽 번호: 처음 · 끝 · 현재 앞뒤 2쪽을 보이고 사이는 … 로 줄인다
  const renderPager = (pages) => {
    pager.hidden = pages <= 1;
    if (pages <= 1) { pager.innerHTML = ''; return; }
    const nums = [];
    for (let i = 1; i <= pages; i++) {
      if (i === 1 || i === pages || Math.abs(i - state.page) <= 2) nums.push(i);
      else if (nums[nums.length - 1] !== '…') nums.push('…');
    }
    const btn = (p, label, aria, disabled) => `<button type="button" class="pg" data-p="${p}" aria-label="${aria}"${disabled ? ' disabled' : ''}>${label}</button>`;
    pager.innerHTML = btn(state.page - 1, '‹', tr('prev_page'), state.page === 1)
      + nums.map((n) => n === '…' ? '<span class="gap">…</span>'
        : `<button type="button" class="pg" data-p="${n}" aria-label="${tr('page', { n })}"${n === state.page ? ' aria-current="page"' : ''}>${n}</button>`).join('')
      + btn(state.page + 1, '›', tr('next_page'), state.page === pages);
  };
  // 검색어나 필터가 바뀌면 1쪽부터 다시 보인다
  const refresh = () => { state.page = 1; syncUrl(); render(); };
  const pick = (p) => { input.value = p.value; closeBox(); refresh(); };
  const syncUrl = (push) => {
    const u = new URL(location.href);
    input.value.trim() ? u.searchParams.set('q', input.value.trim()) : u.searchParams.delete('q');
    state.page > 1 ? u.searchParams.set('p', state.page) : u.searchParams.delete('p');
    const setp = (k, v) => (v ? u.searchParams.set(k, v) : u.searchParams.delete(k));
    setp('t', state.type !== 'all' && state.type); setp('top', state.top && '1'); setp('sh', state.shorts && '1'); setp('life', state.life);
    if (u.href !== location.href) history[push ? 'pushState' : 'replaceState'](null, '', u);
  };
  pager.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-p]'); if (!b || b.disabled) return;
    state.page = Number(b.dataset.p);
    syncUrl(true); render();
    window.scrollTo({ top: 0 });
    count.focus({ preventScroll: true });
  });
  // 뒤로 · 앞으로 가기: 주소의 q · p 를 다시 읽는다
  const readUrl = () => {
    const sp = new URLSearchParams(location.search);
    input.value = (sp.get('q') || '').normalize('NFC');
    state.page = parseInt(sp.get('p'), 10) || 1;
    state.type = sp.get('t') || 'all'; state.top = sp.get('top') === '1'; state.shorts = sp.get('sh') === '1'; state.life = sp.get('life') || '';
    // 칩 표시를 상태에 맞춘다
    document.querySelectorAll('[data-type]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.type === state.type)));
    document.querySelectorAll('[data-toggle]').forEach((x) => x.setAttribute('aria-pressed', String(!!state[x.dataset.toggle])));
    document.querySelectorAll('[data-life]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.life === state.life)));
  };
  window.addEventListener('popstate', () => { readUrl(); closeBox(); render(); });

  let urlTimer;
  input.addEventListener('input', () => {
    suggest(norm(input.value.trim().normalize('NFC'))); state.page = 1; render();
    clearTimeout(urlTimer); urlTimer = setTimeout(() => syncUrl(), 300);
  });
  input.addEventListener('change', () => syncUrl());
  input.addEventListener('keydown', (e) => {
    if (e.isComposing || e.keyCode === 229) return;  // 한국어 조합 중에는 처리하지 않는다
    if (e.key === 'ArrowDown' && box.hidden && input.value.trim()) suggest(norm(input.value.trim().normalize('NFC')));
    const btns = box.hidden ? [] : Array.from(box.children);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!btns.length) return;
      e.preventDefault();
      cursor = (cursor + (e.key === 'ArrowDown' ? 1 : -1) + btns.length) % btns.length;
      btns.forEach((b, i) => { b.classList.toggle('cur', i === cursor); b.setAttribute('aria-selected', String(i === cursor)); });
      input.setAttribute('aria-activedescendant', btns[cursor].id);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (cursor >= 0 && box._list) pick(box._list[cursor]); else { closeBox(); refresh(); input.blur(); }
    } else if (e.key === 'Escape') {
      e.preventDefault();  // type=search 는 Escape 로 입력값을 지운다. 제안 목록만 닫는다
      closeBox();
    }
  });
  box.addEventListener('mousedown', (e) => e.preventDefault());
  box.addEventListener('click', (e) => { const b = e.target.closest('button'); if (b) pick(box._list[Number(b.dataset.i)]); });
  input.addEventListener('blur', closeBox);
  clearBtn.addEventListener('click', () => { input.value = ''; closeBox(); refresh(); input.focus(); });
  document.querySelectorAll('.chips').forEach((g) => g.addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.life !== undefined) {
      state.life = state.life === b.dataset.life ? '' : b.dataset.life;  // 같은 칩을 다시 누르면 해제
      document.querySelectorAll('[data-life]').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.life === state.life)));
    } else if (b.dataset.type) {
      state.type = b.dataset.type;
      document.querySelectorAll('[data-type]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    } else {
      state[b.dataset.toggle] = !state[b.dataset.toggle];
      b.setAttribute('aria-pressed', String(state[b.dataset.toggle]));
    }
    refresh();
  }));

  readUrl();
  fetch('search.json', { cache: 'no-cache' }).then((r) => r.json()).then((data) => {
    const nfc = (s) => s.normalize('NFC');
    items = data.items.map((it) => ({ ...it, title: nfc(it.title), channel: nfc(it.channel), category: nfc(it.category),
      text: nfc(it.text), tags: it.tags.map(nfc) }));
    buildPool(); render(); syncUrl();
    document.addEventListener('removed-ready', render);
  }).catch(() => { count.textContent = tr('index_error'); });
})();
