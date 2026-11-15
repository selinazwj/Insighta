/* Niche requests bypass checkout until the team issues a versioned offer. */
(() => {
  const toggle = document.getElementById('nicheRecruitment');
  if (!toggle) return;
  const form = toggle.form;
  const details = document.getElementById('nicheDetails');
  function sync() {
    const niche = toggle.checked;
    document.body.classList.toggle('niche-request-mode', niche);
    document.getElementById('nicheRequestDetails').hidden = !niche;
    details.required = niche;
    details.disabled = !niche;
    document.querySelector('.listing-payment:not(#nicheSubmitSummary)').hidden = niche;
    document.getElementById('nicheSubmitSummary').hidden = !niche;
    const label = document.getElementById('submitLabel') || document.getElementById('submitBtn');
    if (!document.getElementById('submitBtn').disabled) label.textContent = niche ? 'Submit requirements' : 'Pay & Publish';
    if (typeof WIZARD_TITLES !== 'undefined') {
      WIZARD_TITLES[4] = niche ? 'Submit requirements' : 'Payment & publish';
      const step = typeof currentWizardStep === 'undefined' ? 1 : currentWizardStep;
      if (step === 5) document.getElementById('wizardTitle').textContent = WIZARD_TITLES[4];
      if (step === 4) document.getElementById('wizardNext').textContent = niche ? 'Review request' : 'Continue to payment';
    }
    const labels = document.querySelectorAll('.wizard-label');
    if (labels.length) labels[labels.length - 1].textContent = niche ? 'Submit' : 'Payment';
  }
  toggle.addEventListener('change', sync);
  form.addEventListener('change', sync);
  document.getElementById('wizardNext').addEventListener('click', sync);
  document.getElementById('wizardBack').addEventListener('click', sync);
  document.getElementById('listingPlanAudience')?.addEventListener('change', event => {
    if (event.target.value !== 'broad') { toggle.checked = true; sync(); }
  });
  sync();
})();
