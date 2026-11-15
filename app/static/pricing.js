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
    if (typeof document !== 'undefined') {
      const review = document.getElementById('reviewPaymentTotal');
      if (review) review.textContent = '…';
    }
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
        if (!Number(input.target_responses) || (['cash','gift_card'].includes(input.incentive_type) && !Number(input.per_person))) {
          options.box.style.display = 'none';
          options.status.textContent = '';
          for (const el of Object.values(options.values)) el.textContent = '—';
          if (typeof document !== 'undefined') {
            const review = document.getElementById('reviewPaymentTotal');
            if (review) review.textContent = '—';
            const summary = document.getElementById('walletQuote');
            if (summary) summary.textContent = '';
          }
          return;
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
            ? `${money(quote.wallet.promo_applied_cents)} credit applied · ${money(quote.wallet.cash_balance_cents)} wallet balance`
            : '';
          const review = document.getElementById('reviewPaymentTotal');
          if (review) review.textContent = money(quote.wallet ? quote.wallet.cash_due_cents : quote.total_cents);
          const label = document.getElementById('reviewPaymentLabel');
          if (label) label.textContent = quote.wallet ? 'Wallet payment' : 'Card payment';
          const rule = document.getElementById('listingFeeRule');
          if (rule) rule.textContent = `Platform fee: ${quote.rate_bps/100}% · ${money(quote.minimum_fee_cents)}/person minimum`;
        }
        options.status.textContent = '';
      } catch (error) {
        if (current !== revision || error.name === 'AbortError') return;
        options.total.value = '';
        if (typeof document !== 'undefined') {
          const summary = document.getElementById('walletQuote');
          if (summary) summary.textContent = '';
          const review = document.getElementById('reviewPaymentTotal');
          if (review) review.textContent = '—';
        }
        options.status.textContent = error.message;
        for (const el of Object.values(options.values)) el.textContent = '—';
      }
    }, 180);
  };
};
