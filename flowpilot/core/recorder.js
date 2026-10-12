// FlowPilot recorder - injected into every page while recording.
// Captures user actions, describes the target element and reports to Python through the
// exposed binding window.__flowpilotRecord(event). Selector ranking happens in Python
// (core/selectors.py); this script only gathers facts about the element.
(() => {
  if (window.__fpRecorderInstalled) return;
  window.__fpRecorderInstalled = true;
  if (window.__fpPaused === undefined) window.__fpPaused = false;
  if (window.__fpPickMode === undefined) window.__fpPickMode = false;

  const send = (ev) => {
    try {
      if (typeof window.__flowpilotRecord === 'function') window.__flowpilotRecord(ev);
    } catch (e) { /* page may be unloading */ }
  };
  const norm = (t) => (t || '').replace(/\s+/g, ' ').trim();
  const q = (v) => '"' + String(v).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';

  const IMPLICIT = {
    A: (el) => (el.hasAttribute('href') ? 'link' : ''), BUTTON: () => 'button', SELECT: () => 'combobox',
    TEXTAREA: () => 'textbox', OPTION: () => 'option', SUMMARY: () => 'button',
    INPUT: (el) => {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (['button', 'submit', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'range') return 'slider';
      if (t === 'number') return 'spinbutton';
      if (t === 'search') return 'searchbox';
      if (['text', 'email', 'tel', 'url', 'password', ''].includes(t)) return 'textbox';
      return '';
    },
  };
  const roleOf = (el) => el.getAttribute('role') || (IMPLICIT[el.tagName] ? IMPLICIT[el.tagName](el) : '');

  const labelOf = (el) => {
    if (el.labels && el.labels.length) return norm(el.labels[0].innerText);
    const wrap = el.closest('label');
    if (wrap) return norm(wrap.innerText);
    return '';
  };

  const nameOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return norm(aria);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const txt = by.split(/\s+/).map((id) => document.getElementById(id)).filter(Boolean).map((n) => n.innerText).join(' ');
      if (norm(txt)) return norm(txt);
    }
    const lab = labelOf(el);
    if (lab) return lab;
    if (el.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes((el.type || '').toLowerCase())) return norm(el.value);
    const txt = norm(el.innerText);
    if (txt && txt.length <= 80) return txt;
    return norm(el.getAttribute('alt') || el.getAttribute('title') || el.getAttribute('placeholder') || '');
  };

  const isDynamic = (s) => !s || s.length > 40 || /\d{4,}/.test(s) || /^(css|sc|jsx|emotion|svelte|jss)-/i.test(s) ||
    /_[a-zA-Z0-9]{5,}$/.test(s) || /^[0-9a-f]{8,}$/i.test(s) || /:r[0-9a-z]+:/i.test(s);

  const cssPath = (el) => {
    const parts = [];
    let node = el;
    for (let depth = 0; node && node.nodeType === 1 && depth < 5; depth++) {
      let part = node.tagName.toLowerCase();
      if (node.id && !isDynamic(node.id)) {
        parts.unshift('#' + CSS.escape(node.id));
        break;
      }
      const cls = Array.from(node.classList).filter((c) => !isDynamic(c)).slice(0, 2);
      if (cls.length) part += '.' + cls.map((c) => CSS.escape(c)).join('.');
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (same.length > 1) part += `:nth-of-type(${same.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      const sel = parts.join(' > ');
      try { if (document.querySelectorAll(sel).length === 1) return sel; } catch (e) { /* ignore */ }
      node = parent;
    }
    return parts.join(' > ');
  };

  const xpathOf = (el) => {
    const parts = [];
    for (let node = el; node && node.nodeType === 1; node = node.parentElement) {
      if (node.id && !isDynamic(node.id)) { parts.unshift(`//*[@id="${node.id}"]`); return parts.join('/'); }
      let i = 1;
      for (let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling) if (sib.tagName === node.tagName) i++;
      parts.unshift(`${node.tagName.toLowerCase()}[${i}]`);
    }
    return '/' + parts.join('/');
  };

  const count = (sel) => { try { return document.querySelectorAll(sel).length; } catch (e) { return 0; } };

  const describe = (el) => {
    const tag = el.tagName.toLowerCase();
    const attrs = {};
    for (const a of Array.from(el.attributes)) {
      if (a.name.startsWith('data-') || ['name', 'type', 'placeholder', 'aria-label', 'title', 'alt', 'href', 'role'].includes(a.name)) {
        attrs[a.name] = a.value;
      }
    }
    const counts = {};
    for (const [k, v] of Object.entries(attrs)) {
      const withTag = !['data-testid', 'data-test-id', 'data-test', 'data-qa', 'data-cy', 'data-automation-id'].includes(k);
      counts[k] = count((withTag ? tag : '') + '[' + k + '=' + q(v) + ']');
    }
    if (el.id) counts.id = count('#' + CSS.escape(el.id));
    const role = roleOf(el);
    const name = nameOf(el);
    const text = norm(el.innerText);
    if (text && text.length <= 50) {
      counts.text = Array.from(document.querySelectorAll('a,button,label,span,[role],input[type=submit]'))
        .filter((n) => norm(n.innerText) === text && !n.contains(el) || n === el).length;
    }
    if (role && name) {
      counts.role_name = Array.from(document.querySelectorAll(el.tagName + (el.getAttribute('role') ? ',[role="' + role + '"]' : '')))
        .filter((n) => roleOf(n) === role && nameOf(n) === name).length;
    }
    const label = labelOf(el);
    if (label) counts.label = Array.from(document.querySelectorAll('input,select,textarea')).filter((n) => labelOf(n) === label).length;
    const css = cssPath(el);
    counts.css = count(css);
    return {
      tag, id: el.id || '', attrs, role, name, text: text.slice(0, 120), label, css, xpath: xpathOf(el), counts,
      inputType: (el.type || '').toLowerCase(), url: location.href,
      rect: (() => { const r = el.getBoundingClientRect();
        return { x: Math.round(r.left + window.scrollX), y: Math.round(r.top + window.scrollY),
                 w: Math.round(r.width), h: Math.round(r.height) }; })(),
    };
  };

  const CLICKABLE = 'a,button,input,select,textarea,label,summary,[role=button],[role=link],[role=menuitem],[role=tab],[role=checkbox],[role=option],[onclick]';
  const clickTarget = (el) => (el.closest && el.closest(CLICKABLE)) || el;
  const isTextField = (el) => (el.tagName === 'TEXTAREA' || el.isContentEditable ||
    (el.tagName === 'INPUT' && ['text', 'email', 'password', 'search', 'tel', 'url', 'number', ''].includes((el.type || 'text').toLowerCase())));

  // ---------------------------------------------------------------- pick element mode
  let overlay = null;
  const showOverlay = (el) => {
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #7C6CF2;' +
        'background:rgba(124,108,242,.15);border-radius:6px;transition:all .05s;';
      document.documentElement.appendChild(overlay);
    }
    const r = el.getBoundingClientRect();
    Object.assign(overlay.style, { left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px', display: 'block' });
  };
  const hideOverlay = () => { if (overlay) overlay.style.display = 'none'; };
  window.__fpSetPick = (on) => { window.__fpPickMode = !!on; if (!on) hideOverlay(); };
  window.__fpDescribe = describe;

  // ---------------------------------------------------------------- self-healing
  // Find the element that best matches a recorded fingerprint when every selector failed.
  // Scores tag, role, accessible name, text, label, stable attributes and position.
  const ATTR_WEIGHT = (k) => (/^data-(testid|test-id|test|qa|cy|automation-id)$/.test(k) ? 4 :
    ['name', 'placeholder', 'aria-label', 'title', 'alt'].includes(k) ? 3 : 1.5);
  window.__fpHeal = (fp) => {
    if (!fp || !fp.tag) return null;
    const tag = String(fp.tag).toUpperCase();
    const seen = new Set();
    const pool = [];
    const add = (els) => { for (const el of els) { if (!seen.has(el) && pool.length < 4000) { seen.add(el); pool.push(el); } } };
    add(document.getElementsByTagName(tag));
    if (fp.role) add(document.querySelectorAll(`[role="${String(fp.role).replace(/"/g, '')}"]`));
    if (fp.role === 'button' || fp.role === 'link') add(document.querySelectorAll('a,button,input[type=submit],input[type=button],[role=button],[role=link]'));
    const fpName = norm(fp.name).toLowerCase();
    const fpText = norm(fp.text).toLowerCase();
    const fpLabel = norm(fp.label).toLowerCase();
    const score = (el) => {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return -1;
      let s = 0;
      if (el.tagName === tag) s += 2;
      if (fp.role && roleOf(el) === fp.role) s += 2;
      if (fpName) {
        const n = nameOf(el).toLowerCase();
        if (n === fpName) s += 5; else if (n && fpName.length > 2 && (n.includes(fpName) || fpName.includes(n))) s += 2.5;
      }
      if (fpText) {
        const t = norm(el.innerText).toLowerCase();
        if (t === fpText) s += 4; else if (t && fpText.length > 2 && t.length < 200 && (t.includes(fpText) || fpText.includes(t))) s += 1.5;
      }
      if (fpLabel && labelOf(el).toLowerCase() === fpLabel) s += 4;
      for (const [k, v] of Object.entries(fp.attrs || {})) {
        if (v && el.getAttribute(k) === v) s += ATTR_WEIGHT(k);
      }
      if (fp.id && el.id === fp.id) s += 3;
      if (fp.rect) {
        const d = Math.hypot(r.left + window.scrollX - fp.rect.x, r.top + window.scrollY - fp.rect.y);
        s += Math.max(0, 2 - d / 250);
      }
      return s;
    };
    let best = null, bestScore = -1, second = -1;
    for (const el of pool) {
      const sc = score(el);
      if (sc > bestScore) { second = bestScore; bestScore = sc; best = el; }
      else if (sc > second) second = sc;
    }
    if (!best) return null;
    return { desc: describe(best), score: Math.round(bestScore * 100) / 100, second: Math.round(Math.max(second, 0) * 100) / 100 };
  };

  document.addEventListener('mousemove', (e) => {
    if (window.__fpPickMode && e.target && e.target.nodeType === 1) showOverlay(clickTarget(e.target));
  }, true);

  // ---------------------------------------------------------------- recording
  const pending = new Map(); // element -> last value (typing is reported once per field)
  const flushTyping = (el) => {
    if (!pending.has(el)) return;
    const value = pending.get(el);
    pending.delete(el);
    send({ kind: 'type', desc: describe(el), value });
  };
  const flushAll = () => { for (const el of Array.from(pending.keys())) flushTyping(el); };

  const block = (e) => { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); };

  document.addEventListener('click', (e) => {
    if (window.__fpPickMode) {
      block(e);
      hideOverlay();
      window.__fpPickMode = false;
      send({ kind: 'pick', desc: describe(clickTarget(e.target)) });
      return;
    }
    if (window.__fpPaused || !e.isTrusted) return;
    const el = clickTarget(e.target);
    if (el.tagName === 'SELECT' || el.tagName === 'OPTION') return;
    if (el.tagName === 'INPUT' && ['checkbox', 'radio', 'file'].includes((el.type || '').toLowerCase())) return;
    if (el.tagName === 'LABEL' && el.control && ['checkbox', 'radio'].includes((el.control.type || '').toLowerCase())) return;
    if (isTextField(el)) return; // a Type step clicks the field anyway
    flushAll();
    send({ kind: e.detail >= 2 ? 'dblclick' : 'click', desc: describe(el), button: e.button });
  }, true);

  for (const type of ['mousedown', 'mouseup', 'pointerdown', 'pointerup']) {
    document.addEventListener(type, (e) => { if (window.__fpPickMode) block(e); }, true);
  }

  document.addEventListener('contextmenu', (e) => {
    if (window.__fpPaused || window.__fpPickMode || !e.isTrusted) return;
    send({ kind: 'right_click', desc: describe(clickTarget(e.target)) });
  }, true);

  document.addEventListener('input', (e) => {
    if (window.__fpPaused || !e.isTrusted) return;
    const el = e.target;
    if (isTextField(el)) pending.set(el, el.isContentEditable ? el.innerText : el.value);
  }, true);

  document.addEventListener('change', (e) => {
    const el = e.target;
    // Some automation tools (and a few UI libraries) fire untrusted change events on <select>.
    if (window.__fpPaused || (!e.isTrusted && el.tagName !== 'SELECT')) return;
    if (el.tagName === 'SELECT') {
      const opt = el.options[el.selectedIndex];
      send({ kind: 'select', desc: describe(el), value: el.value, label: opt ? norm(opt.text) : '' });
    } else if (el.tagName === 'INPUT' && ['checkbox', 'radio'].includes((el.type || '').toLowerCase())) {
      send({ kind: 'check', desc: describe(el), checked: el.checked });
    } else if (el.tagName === 'INPUT' && (el.type || '').toLowerCase() === 'file') {
      send({ kind: 'upload', desc: describe(el), names: Array.from(el.files || []).map((f) => f.name) });
    } else if (isTextField(el)) {
      pending.set(el, el.value);
      flushTyping(el);
    }
  }, true);

  document.addEventListener('focusout', (e) => { if (!window.__fpPaused) flushTyping(e.target); }, true);

  document.addEventListener('keydown', (e) => {
    if (window.__fpPaused || window.__fpPickMode || !e.isTrusted) return;
    if (e.key === 'Enter' || e.key === 'Escape' || e.key === 'Tab') {
      const el = e.target;
      flushTyping(el);
      if (e.key === 'Tab') return; // focus change, nothing to replay
      if (e.key === 'Enter' && el.tagName === 'TEXTAREA') return;
      send({ kind: 'press', key: e.key, desc: el && el.nodeType === 1 && el !== document.body ? describe(el) : null });
    }
  }, true);

  window.addEventListener('beforeunload', flushAll, true);
})();
