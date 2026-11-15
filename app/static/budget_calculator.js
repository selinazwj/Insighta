/* Planning results come exclusively from the server's listing pricing rules. */
(() => {
  const form = document.getElementById('budgetCalculatorForm');
  if (!form) return;
  const byId = id => document.getElementById(id);
  const money = cents => new Intl.NumberFormat('en-US', {style:'currency',currency:'USD'}).format(cents / 100);
  const results = byId('calculatorResults');
  const message = byId('calculatorMessage');
  const button = byId('calculateBudgetButton');
  const apply = byId('calculatorUseTopup');
  let revision = 0;
  let suggested = null;
  form.addEventListener('input', event => {
    if (event.target?.id === 'calculatorStudyType') byId('calculatorRateNote').hidden = true;
    revision++;
    button.disabled = false;
    results.hidden = true;
    suggested = null;
    message.textContent = 'Inputs changed. Calculate again.';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const current = ++revision;
    results.hidden = true;
    suggested = null;
    button.disabled = true;
    message.textContent = 'Calculating…';
    try {
      const response = await fetch('/api/wallet/budget-estimate', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({budget:byId('calculatorBudget').value,
          target_responses:byId('calculatorParticipants').value,
          study_type:byId('calculatorStudyType').value, audience:byId('calculatorAudience').value})
      });
      const data = await response.json();
      if (current !== revision) return;
      if (!response.ok) throw new Error(data.detail || 'Estimate unavailable. Please try again.');
      const feeQuote = data.quote;
      const format = {survey:'Survey',interview:'Remote interview',in_person:'In-person study'}[feeQuote.study_type] || 'Study';
      byId('calculatorRateType').textContent = `${format} platform fee`;
      byId('calculatorRateSummary').textContent = `${feeQuote.rate_bps/100}% · ${money(feeQuote.minimum_fee_cents)}/person minimum`;
      byId('calculatorRateNote').hidden = false;
      if (data.status === 'below_minimum') {
        message.textContent = `Budget too low. Minimum: ${money(data.minimum_budget_cents)}. Increase your budget or reduce participants.${data.manual_quote_required ? ' Niche audiences need a quote.' : ''}`;
        return;
      }
      const q = data.quote, funding = data.funding;
      byId('calculatorReward').textContent = money(q.reward_per_person_cents);
      byId('calculatorFee').textContent = money(q.target_responses * q.recruitment_fee_per_person_cents);
      byId('calculatorFeeDetail').textContent = `${money(q.recruitment_fee_per_person_cents)} per participant · ${q.rate_bps/100}% rate / ${money(q.minimum_fee_cents)} minimum${q.minimum_applied ? ' applied' : ''}`;
      byId('calculatorTotal').textContent = money(q.total_cents);
      byId('calculatorTopupLabel').textContent = data.manual_quote_required ? 'General-audience reference' : 'Suggested top-up';
      byId('calculatorTopup').textContent = money(data.baseline_topup_cents);
      byId('calculatorFundingDetail').textContent = `After ${money(funding.cash_balance_cents)} balance + ${money(funding.promo_applied_cents)} credit.`;
      byId('calculatorNicheNote').hidden = !data.manual_quote_required;
      byId('calculatorRemainder').textContent = `${money(data.remaining_budget_cents)} of your entered budget remains unallocated after rounding rewards to cents. Top-ups have a $1 minimum.`;
      suggested = data.suggested_topup_cents;
      apply.hidden = suggested === null || suggested === 0;
      results.hidden = false;
      message.textContent = data.manual_quote_required ? '' : (suggested === 0 ? 'Your balance covers this estimate.' : '');
    } catch (error) {
      if (current === revision) message.textContent = error.message;
    } finally {
      if (current === revision) button.disabled = false;
    }
  });
  apply.addEventListener('click', () => {
    if (suggested === null || suggested <= 0) return;
    byId('topupAmount').value = (suggested/100).toFixed(2);
    byId('topupForm').scrollIntoView({behavior:'smooth',block:'center'});
    byId('topupAmount').focus({preventScroll:true});
  });
})();
