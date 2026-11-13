(function (global) {
  function current(defaultTab = 'overview') {
    const hash = location.hash.replace(/^#/, '');
    const [tab, ...rest] = hash.split('/');
    return { tab: tab || defaultTab, item: rest.join('/') || null };
  }
  function install(root, onChange, defaultTab = 'overview') {
    const nav = root.querySelector('[role="tablist"]');
    const activate = (tab, focus = false) => {
      const buttons = [...nav.querySelectorAll('[role="tab"]')];
      buttons.forEach(button => {
        const active = button.dataset.tab === tab;
        button.setAttribute('aria-selected', String(active));
        button.tabIndex = active ? 0 : -1;
        if (active && focus) button.focus();
      });
      onChange(current(defaultTab));
    };
    nav.addEventListener('click', event => {
      const button = event.target.closest('[role="tab"]');
      if (!button) return;
      location.hash = `#${button.dataset.tab}`;
    });
    nav.addEventListener('keydown', event => {
      const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
      if (!keys.includes(event.key)) return;
      event.preventDefault();
      const buttons = [...nav.querySelectorAll('[role="tab"]')];
      const index = buttons.indexOf(document.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      location.hash = `#${buttons[next].dataset.tab}`;
      buttons[next].focus();
    });
    global.addEventListener('hashchange', () => activate(current(defaultTab).tab));
    activate(current(defaultTab).tab);
  }
  global.SakhiTabs = { current, install };
})(window);
