document.addEventListener('click', async event => {
  const button = event.target.closest('[data-founder-copy]');
  if (!button || button.disabled) return;
  const status = button.closest('.founder-contact').querySelector('[data-founder-status]');
  button.disabled = true;
  try {
    try {
      await navigator.clipboard.writeText('vfsa@bu.edu');
    } catch {
      const field = document.createElement('textarea');
      field.value = 'vfsa@bu.edu';
      field.readOnly = true;
      field.style.cssText = 'position:fixed;left:-9999px;top:0;';
      document.body.append(field);
      let copied;
      try { field.select(); copied = document.execCommand('copy'); }
      finally { field.remove(); button.focus({preventScroll:true}); }
      if (!copied) throw new Error('Clipboard unavailable');
    }
    status.textContent = 'Email copied';
  } catch {
    status.textContent = 'Select and copy vfsa@bu.edu.';
  } finally {
    button.disabled = false;
  }
});
