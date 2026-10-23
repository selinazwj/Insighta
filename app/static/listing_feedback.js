function setAIBusy(button, busy) {
  if (busy) {
    button.dataset.idleLabel = button.textContent;
    button.disabled = true;
    button.setAttribute('aria-label', 'Generating, please wait');
    button.innerHTML = 'Generating<span class="ai-dots" aria-hidden="true"><span></span><span></span><span></span></span>';
  } else {
    button.disabled = false;
    button.textContent = button.dataset.idleLabel || 'Generate';
    button.removeAttribute('aria-label');
  }
  button.closest('.ai-card')?.setAttribute('aria-busy', String(busy));
}
