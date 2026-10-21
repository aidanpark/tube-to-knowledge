// 사이트 조작: 요약 삭제 · 카테고리 이동, 카테고리 추가 · 삭제. 조작은 대기열(/api/ops)에 올리고 집 컴퓨터가 반영한다.
// 반영되기 전에도 대기 중인 조작을 화면에 미리 적용한다: 삭제한 요약은 숨기고, 이동 · 추가 · 삭제는 「대기 중」으로 표시한다.
// 로컬 미리보기(127.0.0.1)처럼 /api 가 없으면 아무 것도 하지 않는다.
(() => {
  const root = document.documentElement.dataset.root || '';
  const api = `${root}api/ops`;
  const call = (method, opts = {}) => fetch(api + (opts.q || ''), {
    method, credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: opts.body,
  }).then((r) => (r.ok ? r.json() : Promise.reject(r.status)));
  const post = (op) => call('POST', { body: JSON.stringify(op) });
  const cancel = (key) => call('DELETE', { q: `?key=${encodeURIComponent(key)}` });
  const FAIL = '요청에 실패했습니다. 잠시 뒤 다시 시도하십시오.';

  // 상태 전환: 상자 안의 [data-state] 중 하나만 보인다
  const show = (box, state) => box.querySelectorAll(':scope > [data-state]').forEach((el) => { el.hidden = el.dataset.state !== state; });
  const say = (box, text) => { const m = box.querySelector('.rm-msg'); if (m) m.textContent = text || ''; };

  const ops = call('GET').then((d) => d.items).catch(() => []);

  // 목록에서 숨기기 (홈 카드, 카테고리 행). 검색은 search.js 가 removed-ready 이벤트를 받아 다시 그린다
  ops.then((items) => {
    const ids = new Set(items.filter((x) => x.type === 'remove').map((x) => x.id));
    window.removedSet = ids;
    document.dispatchEvent(new CustomEvent('removed-ready'));
    document.querySelectorAll('[data-vid]').forEach((el) => { if (ids.has(el.dataset.vid)) { el.hidden = true; el.dataset.removed = '1'; } });
  });

  // 요약 화면: 카테고리 이동과 삭제
  const manage = document.querySelector('[data-manage]');
  if (manage) {
    const id = manage.dataset.vid, title = manage.dataset.title;
    let key = null;
    const pending = (op) => {
      key = op.key;
      manage.querySelector('[data-pending-text]').textContent = op.type === 'remove'
        ? '삭제 대기 중입니다. 곧 사이트에서 지워집니다.'
        : `‘${op.toName || op.to}’ 카테고리로 이동 대기 중입니다. 곧 반영됩니다.`;
      show(manage, 'pending');
    };
    ops.then((items) => { const op = items.filter((x) => x.id === id).pop(); if (op) pending(op); });
    manage.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-act]'); if (!b) return;
      const act = b.dataset.act;
      say(manage);
      if (act === 'ask') show(manage, 'confirm');
      if (act === 'cancel') show(manage, 'idle');
      if (act === 'remove' || act === 'move') {
        const sel = manage.querySelector('[data-move-to]');
        const op = act === 'remove' ? { type: 'remove', id, title }
          : { type: 'move', id, title, to: sel.value, toName: sel.selectedOptions[0].textContent };
        b.disabled = true;
        post(op).then((r) => pending(r)).catch(() => say(manage, FAIL)).finally(() => { b.disabled = false; });
      }
      if (act === 'undo') {
        b.disabled = true;
        cancel(key).then(() => show(manage, 'idle')).catch(() => say(manage, FAIL)).finally(() => { b.disabled = false; });
      }
    });
  }

  // 카테고리 상세 화면: 카테고리 삭제
  const del = document.querySelector('[data-cat-delete]');
  if (del) {
    const cat = del.dataset.catDelete, name = del.dataset.name;
    let key = null;
    ops.then((items) => { const op = items.find((x) => x.type === 'cat-delete' && x.cat === cat); if (op) { key = op.key; show(del, 'pending'); } });
    del.addEventListener('click', (e) => {
      const b = e.target.closest('button[data-act]'); if (!b) return;
      const act = b.dataset.act;
      say(del);
      if (act === 'ask') show(del, 'confirm');
      if (act === 'cancel') show(del, 'idle');
      if (act === 'remove') {
        b.disabled = true;
        post({ type: 'cat-delete', cat, name }).then((r) => { key = r.key; show(del, 'pending'); })
          .catch(() => say(del, FAIL)).finally(() => { b.disabled = false; });
      }
      if (act === 'undo') {
        b.disabled = true;
        cancel(key).then(() => show(del, 'idle')).catch(() => say(del, FAIL)).finally(() => { b.disabled = false; });
      }
    });
  }

  // 카테고리 목록 화면: 새 카테고리, 대기 중인 추가 · 삭제 표시
  const form = document.querySelector('[data-cat-new]');
  if (form) {
    const box = form.closest('section');
    const list = document.querySelector('[data-pending-cats]');
    const q = new URLSearchParams(location.search);  // 요약 화면의 「이 카테고리 만들기」 가 이름 · 설명을 넘긴다
    if (q.get('name')) form.elements.name.value = q.get('name');
    if (q.get('scope')) form.elements.scope.value = q.get('scope');
    if (q.get('name') || q.get('scope')) { box.scrollIntoView(); form.elements.name.focus(); }

    const card = (op) => {
      const a = document.createElement('div');
      a.className = 'ccard c-gray pending';
      const top = document.createElement('span'); top.className = 'top';
      const tag = document.createElement('span'); tag.className = 'n'; tag.textContent = '추가 대기 중';
      const undo = document.createElement('button'); undo.type = 'button'; undo.className = 'rm-btn'; undo.textContent = '취소';
      top.append(tag, undo);
      const nm = document.createElement('span'); nm.className = 'name'; nm.textContent = op.name;
      const sc = document.createElement('span'); sc.className = 'scope'; sc.textContent = op.scope;
      a.append(top, nm, sc);
      undo.addEventListener('click', () => {
        undo.disabled = true;
        cancel(op.key).then(() => a.remove()).catch(() => { undo.disabled = false; say(box, FAIL); });
      });
      list.append(a);
    };
    ops.then((items) => {
      items.filter((x) => x.type === 'cat-create').forEach(card);
      items.filter((x) => x.type === 'cat-delete').forEach((x) => {
        const el = document.querySelector(`[data-cat-id="${x.cat}"] .n`);
        if (el) el.textContent += ' · 삭제 대기 중';
      });
    });
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      say(box);
      const name = form.elements.name.value.trim(), scope = form.elements.scope.value.trim();
      const taken = [...document.querySelectorAll('[data-cat-id] .name, [data-pending-cats] .name')].some((el) => el.textContent === name);
      if (taken) { say(box, '같은 이름의 카테고리가 이미 있습니다.'); return; }
      const btn = form.querySelector('button[type=submit]');
      btn.disabled = true;
      post({ type: 'cat-create', name, scope })
        .then((r) => { card(r); form.reset(); })
        .catch((s) => say(box, s === 400 ? '이름(20자 이내, / \\ : * ? " < > | 제외)과 설명(150자 이내)을 확인하십시오.' : FAIL))
        .finally(() => { btn.disabled = false; });
    });
  }
})();
