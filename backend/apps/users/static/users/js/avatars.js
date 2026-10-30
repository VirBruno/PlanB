"use strict";
(() => {
  const root = document.querySelector('[data-avatar-endpoint]');
  if (!root) return;
  const cache = new Map();
  let scheduled = false;
  let busy = false;
  const descriptor = node => ({scope: node.dataset.avatarScope, context: node.dataset.avatarContext,
    user: node.dataset.avatarUser, ...(node.dataset.avatarUsername ? {username: node.dataset.avatarUsername} : {})});
  function apply(node, url) {
    node.dataset.avatarReady = 'true';
    if (!url) return;
    const image = new Image();
    image.alt = '';
    image.decoding = 'async';
    image.referrerPolicy = 'no-referrer';
    image.addEventListener('load', () => {
      if (node.isConnected) { node.append(image); node.classList.add('pb-user-avatar--photo'); }
    }, {once: true});
    image.addEventListener('error', () => image.remove(), {once: true});
    image.src = url;
  }
  async function load() {
    scheduled = false;
    if (busy) return;
    const pending = new Map();
    root.querySelectorAll('[data-user-avatar]:not([data-avatar-ready])').forEach(node => {
      const item = descriptor(node), key = JSON.stringify(item);
      const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
      if ((item.scope !== 'self' && !uuid.test(item.context)) ||
          (!['self', 'notification'].includes(item.scope) && !uuid.test(item.user))) {
        node.dataset.avatarReady = 'true';
        return;
      }
      if (cache.has(key) && cache.get(key).expires > Date.now()) apply(node, cache.get(key).url);
      else if (pending.has(key)) pending.get(key).nodes.push(node);
      else pending.set(key, {item, nodes: [node]});
    });
    const batch = [...pending.entries()].slice(0, 100);
    if (!batch.length) return;
    busy = true;
    const csrf = document.querySelector('input[name=csrfmiddlewaretoken]')?.value;
    try {
      const response = await fetch(root.dataset.avatarEndpoint, {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json', 'X-CSRFToken':csrf || ''},
        body:JSON.stringify({avatars:batch.map(([, value]) => value.item)})});
      if (!response.ok || response.redirected) throw new Error();
      const data = await response.json();
      if (!Array.isArray(data.urls) || data.urls.length !== batch.length) throw new Error();
      batch.forEach(([key, value], index) => {
        const url = typeof data.urls[index] === 'string' ? data.urls[index] : null;
        cache.set(key, {url, expires: Date.now() + 240000});
        value.nodes.forEach(node => apply(node, url));
      });
    } catch {
      batch.forEach(([key, value]) => { cache.set(key, {url: null, expires: Date.now() + 10000}); value.nodes.forEach(node => apply(node, null)); });
    } finally { busy = false; schedule(); }
  }
  function schedule() {
    if (!scheduled) { scheduled = true; setTimeout(load, 30); }
  }
  new MutationObserver(schedule).observe(root, {childList:true, subtree:true});
  schedule();
})();
