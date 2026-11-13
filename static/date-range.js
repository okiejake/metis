// The header date range picker.
//
// The pure functions at the top mirror date_range.py so the popup can show the
// range live while the user edits it. Both are pinned to
// tests/fixtures/date_range_cases.json. The browser always resolves with the
// server's date (data-today), never its own clock.
(function () {
  'use strict';

  const GRAINS = ['day', 'month', 'year', 'decade'];
  const RELATIVE_LIMITS = { day: 3650, month: 240, year: 50, decade: 10 };
  const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function pad(value) {
    return String(value).padStart(2, '0');
  }

  function toIso(d) {
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function parseIso(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
    return match ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3])) : null;
  }

  function monthEnd(year, monthIndex) {
    return new Date(year, monthIndex + 1, 0);
  }

  function fmtDay(d) {
    return `${MONTH_SHORT[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
  }

  function formatRange(start, end) {
    return `${fmtDay(start)} – ${fmtDay(end)}`;
  }

  // First and last day of the unit an absolute value names.
  function absoluteUnit(grain, value) {
    if (grain === 'day') {
      const d = parseIso(value);
      return [d, d];
    }
    if (grain === 'month') {
      const [y, m] = String(value).split('-').map(Number);
      return [new Date(y, m - 1, 1), monthEnd(y, m - 1)];
    }
    const y = Number(value);
    if (grain === 'year') {
      return [new Date(y, 0, 1), new Date(y, 11, 31)];
    }
    return [new Date(y, 0, 1), new Date(y + 9, 11, 31)];
  }

  // First and last day of the unit `offset` units from today's.
  function relativeUnit(grain, offset, today) {
    if (grain === 'day') {
      const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + offset);
      return [d, d];
    }
    if (grain === 'month') {
      const first = new Date(today.getFullYear(), today.getMonth() + offset, 1);
      return [first, monthEnd(first.getFullYear(), first.getMonth())];
    }
    if (grain === 'year') {
      const y = today.getFullYear() + offset;
      return [new Date(y, 0, 1), new Date(y, 11, 31)];
    }
    const y = Math.floor(today.getFullYear() / 10) * 10 + offset * 10;
    return [new Date(y, 0, 1), new Date(y + 9, 11, 31)];
  }

  function resolveRule(rule, today) {
    if (rule.mode === 'absolute') {
      return [absoluteUnit(rule.grain, rule.start)[0], absoluteUnit(rule.grain, rule.end)[1]];
    }
    return [relativeUnit(rule.grain, Number(rule.start), today)[0], relativeUnit(rule.grain, Number(rule.end), today)[1]];
  }

  function clampOffset(grain, value) {
    const limit = RELATIVE_LIMITS[grain];
    return Math.max(-limit, Math.min(limit, value));
  }

  function absoluteValue(grain, d) {
    if (grain === 'day') return toIso(d);
    if (grain === 'month') return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
    if (grain === 'year') return String(d.getFullYear());
    return String(Math.floor(d.getFullYear() / 10) * 10);
  }

  function relativeOffset(grain, d, today) {
    if (grain === 'day') {
      const dayMs = 24 * 60 * 60 * 1000;
      const a = Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
      const b = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
      return clampOffset(grain, Math.round((a - b) / dayMs));
    }
    if (grain === 'month') {
      return clampOffset(grain, (d.getFullYear() * 12 + d.getMonth()) - (today.getFullYear() * 12 + today.getMonth()));
    }
    if (grain === 'year') return clampOffset(grain, d.getFullYear() - today.getFullYear());
    return clampOffset(grain, Math.floor(d.getFullYear() / 10) - Math.floor(today.getFullYear() / 10));
  }

  // The nearest rule in the new grain and mode covering the same range.
  function convertRule(rule, grain, mode, today) {
    const [start, end] = resolveRule(rule, today);
    if (mode === 'absolute') {
      return { grain, mode, start: absoluteValue(grain, start), end: absoluteValue(grain, end) };
    }
    return { grain, mode, start: relativeOffset(grain, start, today), end: relativeOffset(grain, end, today) };
  }

  // What a relative offset lands on, shown beside its stepper.
  function stepLabel(grain, offset, today) {
    const [first] = relativeUnit(grain, offset, today);
    if (grain === 'day') return fmtDay(first);
    if (grain === 'month') return `${MONTH_SHORT[first.getMonth()]} ${first.getFullYear()}`;
    if (grain === 'year') return String(first.getFullYear());
    return `${first.getFullYear()}s`;
  }

  const api = {
    GRAINS,
    RELATIVE_LIMITS,
    MONTH_SHORT,
    toIso,
    parseIso,
    formatRange,
    resolveRule,
    convertRule,
    stepLabel,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (typeof document === 'undefined') {
    return;
  }
  window.MetisDateRange = api;

  // ---- The popup ----------------------------------------------------------

  const PANEL_KEY = 'metis_range_panel';

  function readPanelShown() {
    try {
      return window.localStorage.getItem(PANEL_KEY) !== '0';
    } catch (error) {
      return true;
    }
  }

  function writePanelShown(shown) {
    try {
      window.localStorage.setItem(PANEL_KEY, shown ? '1' : '0');
    } catch (error) {
      // Storage can be blocked; the panel just won't be remembered.
    }
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([key, value]) => {
      if (value === false || value === null || value === undefined) return;
      if (key === 'text') node.textContent = value;
      else if (key === 'className') node.className = value;
      else node.setAttribute(key, value === true ? '' : value);
    });
    (children || []).forEach((child) => node.appendChild(child));
    return node;
  }

  function signed(value) {
    if (value > 0) return `+${value}`;
    if (value < 0) return `−${Math.abs(value)}`;
    return '0';
  }

  function init() {
    const dialog = document.getElementById('range-dialog');
    const openButton = document.getElementById('range-button');
    if (!dialog || !openButton || typeof dialog.showModal !== 'function') return;

    const form = dialog.querySelector('form');
    const today = parseIso(dialog.dataset.today) || new Date();
    const firstYear = Number(dialog.dataset.firstYear) || today.getFullYear() - 10;
    const lastYear = today.getFullYear() + 10;
    const label = document.getElementById('range-label');
    const panel = document.getElementById('range-panel');
    const toggle = document.getElementById('range-panel-toggle');
    const segments = Array.from(dialog.querySelectorAll('[data-grain]'));
    const modeRadios = Array.from(dialog.querySelectorAll('input[name="mode"]'));
    const startSlot = document.getElementById('range-start-control');
    const endSlot = document.getElementById('range-end-control');
    const hidden = {
      grain: form.querySelector('input[name="grain"]'),
      start: form.querySelector('input[name="start"]'),
      end: form.querySelector('input[name="end"]'),
    };
    let savedRule;
    try {
      savedRule = JSON.parse(dialog.dataset.rule);
    } catch (error) {
      savedRule = { grain: 'month', mode: 'relative', start: -3, end: 3 };
    }
    let rule = savedRule;

    // A page reached through a dated link opens on that link's dates.
    function openingRule() {
      const [s, e] = resolveRule(savedRule, today);
      const shownStart = dialog.dataset.start;
      const shownEnd = dialog.dataset.end;
      if (shownStart && shownEnd && (shownStart !== toIso(s) || shownEnd !== toIso(e))) {
        return { grain: 'day', mode: 'absolute', start: shownStart, end: shownEnd };
      }
      return { ...savedRule };
    }

    function setPanelShown(shown) {
      panel.hidden = !shown;
      toggle.setAttribute('aria-expanded', shown ? 'true' : 'false');
      toggle.setAttribute('aria-label', shown ? 'Hide filters' : 'Show filters');
      toggle.setAttribute('title', shown ? 'Hide filters' : 'Show filters');
      dialog.classList.toggle('panel-hidden', !shown);
    }

    function absoluteOptions(grain) {
      const groups = [];
      if (grain === 'month') {
        for (let y = firstYear; y <= lastYear; y += 1) {
          const group = el('optgroup', { label: String(y) });
          MONTH_SHORT.forEach((name, i) => {
            group.appendChild(el('option', { value: `${y}-${pad(i + 1)}`, text: `${name} ${y}` }));
          });
          groups.push(group);
        }
      } else if (grain === 'year') {
        for (let y = firstYear; y <= lastYear; y += 1) groups.push(el('option', { value: String(y), text: String(y) }));
      } else {
        const fromDecade = Math.floor(firstYear / 10) * 10;
        for (let y = fromDecade; y <= lastYear; y += 10) groups.push(el('option', { value: String(y), text: `${y}s` }));
      }
      return groups;
    }

    function absoluteControl(which) {
      const value = String(rule[which]);
      const name = which === 'start' ? 'Start' : 'End';
      if (rule.grain === 'day') {
        const input = el('input', { type: 'date', className: 'range-date', 'aria-label': name, value });
        input.addEventListener('change', () => setAbsolute(which, input.value));
        return input;
      }
      const select = el('select', { className: 'range-select', 'aria-label': name }, absoluteOptions(rule.grain));
      if (!Array.from(select.options).some((option) => option.value === value)) {
        select.appendChild(el('option', { value, text: value }));
      }
      select.value = value;
      select.addEventListener('change', () => setAbsolute(which, select.value));
      return select;
    }

    function relativeControl(which) {
      const value = Number(rule[which]);
      const limit = RELATIVE_LIMITS[rule.grain];
      const name = which === 'start' ? 'Start' : 'End';
      const otherValue = Number(rule[which === 'start' ? 'end' : 'start']);
      const minimum = which === 'end' ? Math.max(-limit, otherValue) : -limit;
      const maximum = which === 'start' ? Math.min(limit, otherValue) : limit;
      const down = el('button', {
        type: 'button', className: 'range-step', 'aria-label': `${name} earlier`, disabled: value <= minimum, text: '−',
      });
      const up = el('button', {
        type: 'button', className: 'range-step', 'aria-label': `${name} later`, disabled: value >= maximum, text: '+',
      });
      down.addEventListener('click', () => setRelative(which, value - 1));
      up.addEventListener('click', () => setRelative(which, value + 1));
      return el('div', { className: 'range-stepper', role: 'group', 'aria-label': name }, [
        down,
        el('output', { className: 'range-step-value', text: signed(value) }),
        up,
        el('span', { className: 'range-step-note', text: stepLabel(rule.grain, value, today) }),
      ]);
    }

    function setAbsolute(which, value) {
      if (!value) return;
      rule = { ...rule, [which]: value };
      const [s, e] = resolveRule(rule, today);
      if (s > e) {
        rule = { ...rule, [which === 'start' ? 'end' : 'start']: value };
      }
      render();
    }

    function setRelative(which, value) {
      rule = { ...rule, [which]: clampOffset(rule.grain, value) };
      render();
    }

    function render() {
      segments.forEach((button) => button.setAttribute('aria-pressed', button.dataset.grain === rule.grain ? 'true' : 'false'));
      modeRadios.forEach((radio) => {
        radio.checked = radio.value === rule.mode;
      });
      const make = rule.mode === 'absolute' ? absoluteControl : relativeControl;
      startSlot.replaceChildren(make('start'));
      endSlot.replaceChildren(make('end'));
      const [s, e] = resolveRule(rule, today);
      label.textContent = formatRange(s, e);
      if (typeof api.renderPreview === 'function') {
        api.renderPreview(document.getElementById('range-preview'), s, e, today);
      }
    }

    segments.forEach((button) => {
      button.addEventListener('click', () => {
        rule = convertRule(rule, button.dataset.grain, rule.mode, today);
        render();
      });
    });
    modeRadios.forEach((radio) => {
      radio.addEventListener('change', () => {
        if (radio.checked) {
          rule = convertRule(rule, rule.grain, radio.value, today);
          render();
        }
      });
    });

    toggle.addEventListener('click', () => {
      const shown = panel.hidden;
      setPanelShown(shown);
      writePanelShown(shown);
      render();
    });

    document.getElementById('range-cancel').addEventListener('click', () => dialog.close());

    form.addEventListener('submit', () => {
      hidden.grain.value = rule.grain;
      hidden.start.value = String(rule.start);
      hidden.end.value = String(rule.end);
    });

    openButton.addEventListener('click', () => {
      rule = openingRule();
      setPanelShown(readPanelShown());
      dialog.showModal();
      render();
    });

    window.addEventListener('resize', () => {
      if (dialog.open) render();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
