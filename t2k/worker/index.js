// 요약 사이트 Worker (사람마다 하나: <worker>.<계정 하위 도메인>.workers.dev, deploy.py).
// 정적 파일(site/)은 assets 가 응답하고, 파일이 없는 /api/ops 만 이 코드가 처리한다.
// /api/ops: 사이트 조작 대기열. 요약 삭제 · 이동, 카테고리 추가 · 삭제를 KV(OPS)에 쌓고 집 컴퓨터의 site_ops 가 반영한다.
// 사이트 전체가 Cloudflare Access 로 보호되므로, Access 를 거친 요청에만 붙는 헤더가 없으면 거부한다
// (Access 설정이 빠졌을 때의 안전장치).
// 키는 「시각-임의값」 이라 KV 목록이 조작한 순서대로 나온다. 내용은 metadata 에 둔다 (목록 조회 한 번으로 모두 읽는다).
const ID_RE = /^[A-Za-z0-9_-]{11}$/;
const CAT_RE = /^\d{4}$/;
const NAME_RE = /^[^/\\:*?"<>|\x00-\x1f]{1,20}$/;  // config.NAME_RE 와 같다 (폴더 이름이 된다)
const SCOPE_MAX = 150;
const OTHER_ID = '0099';
const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
});
const viaAccess = (request) =>
  request.headers.get('cf-access-authenticated-user-email') || request.headers.get('cf-access-jwt-assertion');
const text = (v, max) => String(v || '').trim().slice(0, max);

// 종류별로 받은 값을 검사해 metadata 를 만든다. 잘못된 값이면 오류 문구를 반환한다
function validate(b) {
  if (b.type === 'remove') {
    if (!ID_RE.test(b.id || '')) return 'bad id';
    return { type: 'remove', id: b.id, title: text(b.title, 200) };
  }
  if (b.type === 'move') {
    if (!ID_RE.test(b.id || '')) return 'bad id';
    if (!CAT_RE.test(b.to || '')) return 'bad category';
    return { type: 'move', id: b.id, title: text(b.title, 200), to: b.to, toName: text(b.toName, 20) };
  }
  if (b.type === 'cat-create') {
    const name = text(b.name, 40), scope = String(b.scope || '').split(/\s+/).join(' ').trim();
    if (!NAME_RE.test(name) || name.startsWith('.')) return 'bad name';
    if (!scope || scope.length > SCOPE_MAX) return 'bad scope';
    return { type: 'cat-create', name, scope };
  }
  if (b.type === 'cat-delete') {
    if (!CAT_RE.test(b.cat || '') || b.cat === OTHER_ID) return 'bad category';
    return { type: 'cat-delete', cat: b.cat, name: text(b.name, 20) };
  }
  return 'bad type';
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === '/api/ops') return ops(request, env, url);
    // assets 는 html_handling none 이라 / 를 index.html 로 연결하지 않는다 (deploy.py). 폴더 주소만 여기서 연결한다
    if (url.pathname.endsWith('/')) {
      url.pathname += 'index.html';
      return env.ASSETS.fetch(new Request(url, request));
    }
    return env.ASSETS.fetch(request);
  },
};

async function ops(request, env, url) {
  if (!viaAccess(request)) return json({ error: 'forbidden' }, 403);
  if (request.method === 'GET') {
    const out = [];
    let cursor;
    do {
      const page = await env.OPS.list({ cursor });
      for (const k of page.keys) {
        const meta = k.metadata || {};
        // 이전 삭제 버튼의 키(동영상 ID, type 없음)는 삭제로 읽는다
        out.push(!meta.type && ID_RE.test(k.name) ? { key: k.name, type: 'remove', id: k.name, ...meta } : { key: k.name, ...meta });
      }
      cursor = page.list_complete ? undefined : page.cursor;
    } while (cursor);
    return json({ items: out });
  }
  if (request.method === 'POST') {
    let body;
    try { body = await request.json(); } catch { return json({ error: 'bad json' }, 400); }
    const meta = validate(body || {});
    if (typeof meta === 'string') return json({ error: meta }, 400);
    meta.time = new Date().toISOString();
    const key = `${Date.now().toString().padStart(15, '0')}-${crypto.randomUUID().slice(0, 8)}`;
    await env.OPS.put(key, '1', { metadata: meta });  // metadata 는 1024 바이트까지다 (제목 200자 · 설명 150자로 제한)
    return json({ ok: true, key, ...meta });
  }
  if (request.method === 'DELETE') {
    const key = url.searchParams.get('key') || '';
    if (!/^[0-9]{15}-[0-9a-f]{8}$/.test(key) && !ID_RE.test(key)) return json({ error: 'bad key' }, 400);
    await env.OPS.delete(key);
    return json({ ok: true, key });
  }
  return json({ error: 'method not allowed' }, 405);
}
