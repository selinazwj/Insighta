(() => {
  document.querySelectorAll('[data-didit-verification]').forEach(card => {
    if (card.dataset.initialized) return;
    card.dataset.initialized = 'true';
    const badge = card.querySelector('[data-didit-badge]');
    const message = card.querySelector('[data-didit-message]');
    const start = card.querySelector('[data-didit-start]');
    const refresh = card.querySelector('[data-didit-refresh]');
    const sessionId = card.dataset.sessionId;
    let pollTimer, polls = 0, starting = false, loading = false;
    const statusUrl = '/api/verification/status' + (sessionId ? '?session_id=' + encodeURIComponent(sessionId) : '');
    const labels = {verified: 'Identity verified', pending: 'In progress', rejected: 'Not verified', expired: 'Expired', unverified: 'Not started'};
    const descriptions = {
      verified: 'Your identity check is complete. Your participant identity status is verified.',
      pending: 'Your identity check is still processing. We will update this status when the result arrives.',
      rejected: 'This identity check was not approved or was not completed. You can try again.',
      expired: 'Your identity verification has expired. Start a new check to verify again.',
      unverified: 'Complete an identity check with Didit. Email verification is a separate account check.'
    };
    async function loadStatus(resetPolls = false) {
      if (loading || starting) return;
      if (resetPolls) polls = 0;
      clearTimeout(pollTimer);
      loading = true;
      refresh.disabled = true;
      try {
        const res = await fetch(statusUrl, {credentials: 'same-origin', cache: 'no-store'});
        if (!res.ok) throw new Error(res.status === 401 ? 'Please sign in again to view verification.' : 'Unable to load verification status. Please refresh to try again.');
        const data = await res.json();
        const state = sessionId && data.didit ? data.didit.status : data.identity_status;
        card.dataset.state = state;
        badge.textContent = labels[state] || 'Checking';
        message.textContent = descriptions[state] || descriptions.pending;
        const inReview = state === 'pending' && data.didit?.provider_status === 'In Review';
        start.hidden = state === 'verified';
        start.disabled = !data.didit_configured || inReview;
        start.textContent = inReview ? 'Under review' : state === 'pending' ? 'Continue with Didit' : ['rejected', 'expired'].includes(state) ? 'Try verification again' : 'Verify with Didit';
        if (!data.didit_configured && state !== 'verified') {
          message.textContent += ' Identity verification is currently unavailable. Please check back later.';
        }
        if (state === 'pending' && polls++ < 12) {
          pollTimer = setTimeout(() => loadStatus(), 5000);
        } else if (state === 'pending') {
          message.textContent += ' You can leave this page and check again later.';
        }
      } catch (error) {
        message.textContent = error.message;
        badge.textContent = 'Status unavailable';
      } finally {
        loading = false;
        refresh.disabled = false;
        card.setAttribute('aria-busy', 'false');
      }
    }
    start.addEventListener('click', async () => {
      if (starting) return;
      starting = true;
      clearTimeout(pollTimer);
      start.disabled = true;
      refresh.disabled = true;
      start.textContent = 'Opening Didit…';
      message.textContent = 'Preparing your secure identity check.';
      try {
        const res = await fetch('/api/verification/didit/session', {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/json'}, body: '{}'
        });
        const data = await res.json();
        if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to start verification. Please try again.');
        if (data.status === 'verified') {
          // The existing return page may refer to an older session. Go to the
          // result of the current server-created session instead.
          window.location.assign('/verify/didit/return' + (data.session_id ? '?verificationSessionId=' + encodeURIComponent(data.session_id) : ''));
          return;
        }
        const target = new URL(data.url);
        if (target.protocol !== 'https:') throw new Error('Unable to open verification. Please try again.');
        window.location.assign(target.href);
      } catch (error) {
        message.textContent = error.message || 'Unable to start verification. Please try again.';
        start.textContent = 'Try again';
        start.disabled = false;
        refresh.disabled = false;
        starting = false;
      }
    });
    refresh.addEventListener('click', () => loadStatus(true));
    window.addEventListener('pageshow', event => { if (event.persisted) { starting = false; loadStatus(true); } });
    loadStatus();
  });
})();
