(() => {
  const nav = document.querySelector('[data-site-nav]');
  if (!nav) return;
  const toggle = nav.querySelector('.site-menu-toggle');
  const closeMenu = () => {
    nav.removeAttribute('data-menu-open');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', 'Open navigation menu');
  };
  toggle.addEventListener('click', () => {
    const open = !nav.hasAttribute('data-menu-open');
    nav.toggleAttribute('data-menu-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? 'Close navigation menu' : 'Open navigation menu');
  });
  nav.addEventListener('keydown', event => {
    if (event.key === 'Escape' && nav.hasAttribute('data-menu-open')) {
      closeMenu(); toggle.focus();
    }
  });
  nav.querySelectorAll('.site-links a').forEach(link => link.addEventListener('click', closeMenu));
  nav.querySelectorAll('[data-site-role]').forEach(button => button.addEventListener('click', () => {
    const role = button.dataset.siteRole;
    if (typeof window.setHomepageMode === 'function') {
      window.setHomepageMode(role); return;
    }
    nav.querySelectorAll('[data-site-role]').forEach(tab => {
      const active = tab.dataset.siteRole === role;
      tab.classList.toggle('active', active); tab.setAttribute('aria-pressed', String(active));
    });
    nav.querySelector('.mode-login-link').href = `/login?role=${role}`;
    nav.querySelector('.mode-register-link').href = `/register?role=${role}`;
  }));
})();
