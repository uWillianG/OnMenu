/* Shared navigation and keyboard behavior. Business interactions stay in their own scripts. */
(() => {
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('menu-toggle');
  const overlay = document.getElementById('sidebar-overlay');
  const closeButton = document.getElementById('sidebar-close');
  const desktop = window.matchMedia('(min-width: 1100px)');
  const isStaff = document.body.classList.contains('layout-staff');
  const heading = document.querySelector('.hero-inner h1.is-long-name');
  if (heading) {
    let measuredWidth = 0;
    function fitHeading() {
      heading.style.fontSize = '';
      let size = parseFloat(getComputedStyle(heading).fontSize);
      const minimum = window.matchMedia('(max-width: 760px)').matches ? 16 : 22;
      while (size > minimum && heading.clientHeight > parseFloat(getComputedStyle(heading).lineHeight) * 3.1) {
        size -= 1;
        heading.style.fontSize = size + 'px';
      }
      measuredWidth = heading.clientWidth;
    }
    new ResizeObserver(() => {
      if (heading.clientWidth !== measuredWidth) fitHeading();
    }).observe(heading);
    fitHeading();
    document.fonts?.ready.then(fitHeading);
  }
  const focusable = 'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';
  const canFocus = el => !el.disabled && !el.closest('[inert]') && el.getClientRects().length > 0;
  const controls = root => [...root.querySelectorAll(focusable)].filter(canFocus);
  let drawerOpen = false;
  let dialog = null;
  let returnFocus = null;

  function trap(event, root) {
    const items = controls(root);
    if (!items.length) { event.preventDefault(); root.focus(); return; }
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && (document.activeElement === first || !root.contains(document.activeElement))) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !root.contains(document.activeElement))) {
      event.preventDefault(); first.focus();
    }
  }

  function setDrawer(open, restore = true) {
    if (!sidebar || !toggle || !overlay) return;
    const persistent = isStaff && desktop.matches;
    drawerOpen = open && !persistent;
    sidebar.classList.toggle('is-open', drawerOpen);
    sidebar.inert = !(persistent || drawerOpen);
    sidebar.setAttribute('aria-hidden', String(!(persistent || drawerOpen)));
    toggle.setAttribute('aria-expanded', String(drawerOpen));
    overlay.hidden = !drawerOpen;
    overlay.classList.toggle('is-visible', drawerOpen);
    document.body.classList.toggle('no-scroll', drawerOpen);
    if (drawerOpen) {
      sidebar.setAttribute('role', 'dialog');
      sidebar.setAttribute('aria-modal', 'true');
      closeButton?.focus();
    } else {
      sidebar.removeAttribute('role');
      sidebar.removeAttribute('aria-modal');
      if (restore && !persistent) toggle.focus();
    }
  }

  toggle?.addEventListener('click', () => setDrawer(!drawerOpen));
  closeButton?.addEventListener('click', () => setDrawer(false));
  overlay?.addEventListener('click', () => setDrawer(false));
  desktop.addEventListener('change', () => setDrawer(false, false));
  setDrawer(false, false);

  function syncDialog() {
    const next = [...document.querySelectorAll('[role="dialog"]:not(#sidebar)')]
      .filter(el => !el.hidden && el.getAttribute('aria-hidden') !== 'true' && el.getClientRects().length)
      .at(-1) || null;
    if (next === dialog) return;
    if (next) {
      if (!dialog) returnFocus = document.activeElement;
      dialog = next;
      if (!dialog.hasAttribute('tabindex')) dialog.setAttribute('tabindex', '-1');
      (controls(dialog)[0] || dialog).focus({ preventScroll: true });
    } else {
      dialog = null;
      if (returnFocus?.isConnected && canFocus(returnFocus)) returnFocus.focus({ preventScroll: true });
      returnFocus = null;
    }
  }

  new MutationObserver(syncDialog).observe(document.body, {
    subtree: true, attributes: true, attributeFilter: ['hidden', 'aria-hidden'], childList: true,
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Tab' && dialog) trap(event, dialog);
    else if (event.key === 'Tab' && drawerOpen) trap(event, sidebar);
    if (event.key === 'Escape' && drawerOpen && !dialog) setDrawer(false);
  });
  document.addEventListener('focusin', event => {
    if (dialog && !dialog.contains(event.target)) (controls(dialog)[0] || dialog).focus({ preventScroll: true });
  });
})();
