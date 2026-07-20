(function () {
  const storageKey = 'insighta_anonymous_id';
  const config = window.INSIGHTA_TRACKING || {};
  let anonymousId = localStorage.getItem(storageKey);
  if (!anonymousId) {
    anonymousId = 'anon_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem(storageKey, anonymousId);
  }

  const impressed = new Set();

  function payload(eventName, extra) {
    const data = Object.assign({
      event_name: eventName,
      anonymous_id: anonymousId,
      page_path: window.location.pathname + window.location.search,
      metadata: {
        title: document.title || '',
        referrer: document.referrer || '',
        surface: config.surface || '',
        user_role: config.userRole || config.role || '',
      }
    }, extra || {});
    data.metadata = Object.assign({}, data.metadata || {}, (extra && extra.metadata) || {});
    return data;
  }

  function send(eventName, extra, beacon) {
    const body = JSON.stringify(payload(eventName, extra));
    if (beacon && navigator.sendBeacon) {
      const blob = new Blob([body], { type: 'application/json' });
      navigator.sendBeacon('/api/events', blob);
      return Promise.resolve();
    }
    return fetch('/api/events', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body
    }).catch(function () {});
  }

  window.insightaTrack = send;

  send('page_view', {
    target_type: config.targetType || null,
    target_id: config.targetId || null,
    metadata: Object.assign({ source: config.source || config.surface || '' }, config.metadata || {})
  });

  if (config.listingId) {
    send('study_impression', {
      target_type: 'survey',
      target_id: config.listingId,
      metadata: Object.assign({
        study_title: config.studyTitle || '',
        source: config.source || config.surface || 'Listing'
      }, config.metadata || {})
    });
  }

  window.insightaObserveStudyCards = function (selector, metaFn) {
    if (!window.IntersectionObserver) return;
    const nodes = document.querySelectorAll(selector);
    if (!nodes.length) return;
    const observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting || entry.intersectionRatio < 0.5) return;
        const el = entry.target;
        const key = el.getAttribute('data-study-id') || el.id || '';
        if (!key || impressed.has(key)) return;
        impressed.add(key);
        const extra = typeof metaFn === 'function' ? (metaFn(el) || {}) : {};
        send('study_impression', extra);
        observer.unobserve(el);
      });
    }, { threshold: [0.5] });
    nodes.forEach(function (node) { observer.observe(node); });
  };

  let hiddenSent = false;
  function markHidden() {
    if (hiddenSent) return;
    hiddenSent = true;
    send('page_exit', {
      target_type: config.targetType || null,
      target_id: config.targetId || null,
      metadata: Object.assign({ source: config.source || config.surface || '' }, config.metadata || {})
    }, true);
  }
  window.addEventListener('pagehide', markHidden);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') markHidden();
  });
})();
