(function () {
  const storageKey = 'insighta_anonymous_id';
  const config = window.INSIGHTA_TRACKING || {};
  let anonymousId = localStorage.getItem(storageKey);
  if (!anonymousId) {
    anonymousId = 'anon_' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem(storageKey, anonymousId);
  }

  function payload(eventName, extra) {
    const data = Object.assign({
      event_name: eventName,
      anonymous_id: anonymousId,
      page_path: window.location.pathname + window.location.search,
      metadata: {
        title: document.title || '',
        referrer: document.referrer || '',
        surface: config.surface || '',
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

  send('page_viewed', {
    target_type: config.targetType || null,
    target_id: config.targetId || null,
    metadata: config.metadata || {}
  });

  if (config.listingId) {
    send('study_card_viewed', {
      target_type: 'survey',
      target_id: config.listingId,
      metadata: Object.assign({ study_title: config.studyTitle || '' }, config.metadata || {})
    });
  }

  let hiddenSent = false;
  function markHidden() {
    if (hiddenSent) return;
    hiddenSent = true;
    send('page_hidden', {
      target_type: config.targetType || null,
      target_id: config.targetId || null,
      metadata: config.metadata || {}
    }, true);
  }
  window.addEventListener('pagehide', markHidden);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') markHidden();
  });
})();
