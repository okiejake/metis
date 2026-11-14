// The header's theme toggle: one button cycling light (sun), dark (moon) and
// system (sun-moon). The choice is kept per browser under `metis_theme`; the
// inline script in base.html's <head> applies it before the page paints.
(function () {
  'use strict';

  const STORAGE_KEY = 'metis_theme';
  const CYCLE = ['light', 'dark', 'system'];
  const ICONS = { light: 'sun', dark: 'moon', system: 'sun-moon' };

  // Anything other than light or dark means "follow the system".
  function normalizeTheme(value) {
    return value === 'light' || value === 'dark' ? value : 'system';
  }

  function nextTheme(value) {
    const current = CYCLE.indexOf(normalizeTheme(value));
    return CYCLE[(current + 1) % CYCLE.length];
  }

  function resolveTheme(value, prefersDark) {
    const theme = normalizeTheme(value);
    if (theme === 'system') return prefersDark ? 'dark' : 'light';
    return theme;
  }

  function themeIcon(value) {
    return ICONS[normalizeTheme(value)];
  }

  const api = { normalizeTheme, nextTheme, resolveTheme, themeIcon };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (typeof document === 'undefined') {
    return;
  }

  function readTheme() {
    try {
      return normalizeTheme(window.localStorage.getItem(STORAGE_KEY));
    } catch (error) {
      return 'system';
    }
  }

  function writeTheme(value) {
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch (error) {
      // Storage can be blocked; the choice then lasts only for this page.
    }
  }

  function init() {
    const button = document.getElementById('theme-toggle');
    const media = typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null;
    let theme = readTheme();

    function apply() {
      document.documentElement.setAttribute('data-theme', resolveTheme(theme, Boolean(media && media.matches)));
      if (!button) return;
      const label = `theme: ${theme}`;
      button.setAttribute('aria-label', label);
      button.setAttribute('title', label);
      button.innerHTML = `<i data-lucide="${themeIcon(theme)}" class="menu-icon" aria-hidden="true"></i>`;
      if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
    }

    if (button) {
      button.addEventListener('click', () => {
        theme = nextTheme(theme);
        writeTheme(theme);
        apply();
      });
    }
    if (media && typeof media.addEventListener === 'function') {
      media.addEventListener('change', () => {
        if (theme === 'system') apply();
      });
    }
    apply();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
