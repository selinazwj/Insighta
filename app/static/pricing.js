/* Render server quotes; no pricing formulas live in the browser. */
window.InsightaPricing = function (options) {
  let revision = 0;
  let timer;
  let controller;
  const money = cents => new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD'}).format(cents / 100);
  return function refreshQuote() {
    const current = ++revision;
    clearTimeout(timer);
    controller?.abort();
    options.box.style.display = 'block';
    options.total.value = '';
    for (const el of Object.values(options.values)) el.textContent = '…';
    options.status.textContent = 'Updating quote…';
    timer = setTimeout(async () => {
      controller = new AbortController();
      try {
        const input = {...options.input()};
        if (typeof document !== 'undefined') {
          input.payment_method = document.querySelector('[name="payment_method"]:checked')?.value || 'stripe';
          input.use_promo = document.getElementById('usePromoCredits')?.checked === true;
        }
        const response = await fetch('/api/calculate-price', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(input), signal: controller.signal
        });
        const quote = await response.json();
        if (current !== revision) return;
        if (!response.ok) throw new Error(quote.detail || 'Quote unavailable. Please try again.');
        options.values.reward.textContent = money(quote.reward_per_person_cents);
        options.values.fee.textContent = money(quote.recruitment_fee_per_person_cents);
        options.values.urgency.textContent = money(quote.urgency_amount_cents);
        options.values.total.textContent = money(quote.total_cents);
        options.total.value = (quote.total_cents / 100).toFixed(2);
        if (typeof document !== 'undefined') {
          const summary = document.getElementById('walletQuote');
          if (summary) summary.textContent = quote.wallet
            ? `Promotional credit: ${money(quote.wallet.promo_applied_cents)}. Wallet cash due: ${money(quote.wallet.cash_due_cents)}. Available cash: ${money(quote.wallet.cash_balance_cents)}.`
            : 'Card checkout charges the full quoted amount. Promotional credit is available with wallet payment.';
        }
        options.status.textContent = `${quote.rate_bps / 100}% recruitment rate; ${money(quote.minimum_fee_cents)} minimum per person${quote.minimum_applied ? ' applied' : ''}. Participants receive their full stated reward.`;
      } catch (error) {
        if (current !== revision || error.name === 'AbortError') return;
        options.total.value = '';
        if (typeof document !== 'undefined') {
          const summary = document.getElementById('walletQuote');
          if (summary) summary.textContent = '';
        }
        options.status.textContent = error.message;
        for (const el of Object.values(options.values)) el.textContent = '—';
      }
    }, 180);
  };
};
