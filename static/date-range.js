// The header date range picker.
//
// The pure functions at the top mirror date_range.py so the popup can show the
// range live while the user edits it. Both are pinned to
// tests/fixtures/date_range_cases.json. The browser always resolves with the
// server's date (data-today), never its own clock.
(function () {
  'use strict';

  const GRAINS = ['day', 'month', 'quarter', 'year', 'decade'];
  const RELATIVE_LIMITS = { day: 3650, month: 240, quarter: 80, year: 50, decade: 10 };
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
    if (grain === 'quarter') {
      const [yText, qText] = String(value).split('-Q');
      const y = Number(yText);
      const q = Number(qText);
      return [new Date(y, q * 3 - 3, 1), monthEnd(y, q * 3 - 1)];
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
    if (grain === 'quarter') {
      const first = new Date(today.getFullYear(), Math.floor(today.getMonth() / 3) * 3 + offset * 3, 1);
      return [first, monthEnd(first.getFullYear(), first.getMonth() + 2)];
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
    if (grain === 'quarter') return `${d.getFullYear()}-Q${Math.floor(d.getMonth() / 3) + 1}`;
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
    if (grain === 'quarter') {
      const quarterIndex = (x) => x.getFullYear() * 4 + Math.floor(x.getMonth() / 3);
      return clampOffset(grain, quarterIndex(d) - quarterIndex(today));
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

  // Two rules are the same filter when grain, mode and both ends match.
  function sameRule(a, b) {
    if (!a || !b || a.grain !== b.grain || a.mode !== b.mode) return false;
    if (a.mode === 'relative') return Number(a.start) === Number(b.start) && Number(a.end) === Number(b.end);
    return String(a.start) === String(b.start) && String(a.end) === String(b.end);
  }

  // A typed relative offset: "3", "+3", "-3" or "−3". Anything else is null.
  function parseOffset(text) {
    const match = /^([+\-−]?)(\d+)$/.exec(String(text).trim());
    if (!match) return null;
    const value = Number(match[2]);
    return match[1] === '-' || match[1] === '−' ? -value : value;
  }

  // What a relative offset lands on, shown beside its stepper.
  function stepLabel(grain, offset, today) {
    const [first] = relativeUnit(grain, offset, today);
    if (grain === 'day') return fmtDay(first);
    if (grain === 'month') return `${MONTH_SHORT[first.getMonth()]} ${first.getFullYear()}`;
    if (grain === 'quarter') return `Q${Math.floor(first.getMonth() / 3) + 1} ${first.getFullYear()}`;
    if (grain === 'year') return String(first.getFullYear());
    return `${first.getFullYear()}s`;
  }


  // ---- Preview: which view, and which months --------------------------------

  // Months the range touches: a month counts if any day of the range is in it.
  function monthsTouched(start, end) {
    return (end.getFullYear() * 12 + end.getMonth()) - (start.getFullYear() * 12 + start.getMonth()) + 1;
  }

  // The view depends only on the months covered, never on the grain.
  function previewView(count) {
    if (count <= 3) return 'full';
    if (count <= 24) return 'mini';
    return 'years';
  }

  // Quarters the range touches, plus one before and one after, so January is
  // always in column 1 of the mini view. Each row is [year, first month 1-12].
  function quarterRows(start, end) {
    const first = start.getFullYear() * 4 + Math.floor(start.getMonth() / 3) - 1;
    const last = end.getFullYear() * 4 + Math.floor(end.getMonth() / 3) + 1;
    const rows = [];
    for (let q = first; q <= last; q += 1) rows.push([Math.floor(q / 4), (q % 4) * 3 + 1]);
    return rows;
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
    parseOffset,
    sameRule,
    monthsTouched,
    previewView,
    quarterRows,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (typeof document === 'undefined') {
    return;
  }
  window.MetisDateRange = api;


  // ---- Preview rendering ----------------------------------------------------

  const WEEKDAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
  const MONTH_LETTERS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];

  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }

  function div(className, text) {
    const node = document.createElement('div');
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // One month's day grid. `full` adds day numbers and weekday letters.
  function monthBlock(year, monthIndex, start, end, today, opts) {
    const block = div(`rp-month${opts.dim ? ' rp-dim' : ''}${opts.full ? '' : ' rp-small'}`);
    const title = opts.full ? `${new Date(year, monthIndex, 1).toLocaleDateString('en-US', { month: 'long' })} ${year}` : `${MONTH_SHORT[monthIndex]} ${year}`;
    block.appendChild(div('rp-title', title));
    if (opts.full) {
      const weekdays = div('rp-weekdays');
      WEEKDAYS.forEach((letter) => weekdays.appendChild(div('', letter)));
      block.appendChild(weekdays);
    }
    const grid = div('rp-days');
    const lead = new Date(year, monthIndex, 1).getDay();
    const days = monthEnd(year, monthIndex).getDate();
    for (let i = 0; i < lead; i += 1) grid.appendChild(div('rp-day rp-blank'));
    for (let dayNumber = 1; dayNumber <= days; dayNumber += 1) {
      const d = new Date(year, monthIndex, dayNumber);
      let className = 'rp-day';
      if (d >= start && d <= end) {
        className += ' rp-in';
      }
      if (sameDay(d, today)) className += ' rp-today';
      grid.appendChild(div(className, opts.full ? String(dayNumber) : undefined));
    }
    block.appendChild(grid);
    block.style.setProperty('--rp-cell', `${opts.cell}px`);
    return block;
  }

  function setFade(container, fade) {
    const mask = fade
      ? `linear-gradient(to bottom, transparent 0, #000 ${fade}px, #000 calc(100% - ${fade}px), transparent 100%)`
      : '';
    container.style.maskImage = mask;
    container.style.webkitMaskImage = mask;
  }

  function renderPreview(container, start, end, today) {
    if (!container) return;
    const H = container.clientHeight;
    const count = monthsTouched(start, end);
    const view = previewView(count);
    const content = div(`rp-content rp-${view}`);

    if (view === 'full') {
      // Range months take ~80% of H; the month before and after peek in, faded.
      const monthH = Math.min(230, (H * 0.8) / count);
      const cell = Math.max(10, Math.floor((monthH - 54) / 6));
      for (let i = -1; i <= count; i += 1) {
        const first = new Date(start.getFullYear(), start.getMonth() + i, 1);
        content.appendChild(monthBlock(first.getFullYear(), first.getMonth(), start, end, today, {
          full: true, dim: i < 0 || i >= count, cell,
        }));
      }
      setFade(container, H * 0.12);
    } else if (view === 'mini') {
      // Range rows plus half a neighbour row at each end fill H.
      const rows = quarterRows(start, end);
      const rowH = H / (rows.length - 2 + 1);
      const gap = Math.max(6, rowH * 0.12);
      const cell = Math.max(3, Math.floor((rowH - gap - 22) / 6));
      const firstShown = new Date(start.getFullYear(), start.getMonth(), 1);
      content.style.rowGap = `${gap}px`;
      rows.forEach(([year, firstMonth], index) => {
        for (let k = 0; k < 3; k += 1) {
          const monthIndex = firstMonth - 1 + k;
          const neighbour = index === 0 || index === rows.length - 1;
          const outside = new Date(year, monthIndex, 1) < firstShown || new Date(year, monthIndex, 1) > end;
          content.appendChild(monthBlock(year, monthIndex, start, end, today, { full: false, dim: neighbour || outside, cell }));
        }
      });
      setFade(container, rowH * 0.5);
    } else {
      // One row per year, a block per month; rows share H.
      const years = end.getFullYear() - start.getFullYear() + 1;
      const gap = years > 20 ? 2 : 4;
      const rowH = Math.min(28, (H - gap * (years - 1)) / years);
      content.style.setProperty('--rp-row', `${rowH}px`);
      content.style.rowGap = `${gap}px`;
      for (let year = start.getFullYear(); year <= end.getFullYear(); year += 1) {
        const row = div('rp-year');
        row.appendChild(div('rp-year-label', String(year)));
        for (let m = 0; m < 12; m += 1) {
          let className = 'rp-block';
          if (new Date(year, m, 1) <= end && monthEnd(year, m) >= start) className += ' rp-in';
          if (year === today.getFullYear() && m === today.getMonth()) className += ' rp-today';
          row.appendChild(div(className, rowH >= 14 ? MONTH_LETTERS[m] : undefined));
        }
        content.appendChild(row);
      }
      setFade(container, 0);
    }
    container.replaceChildren(content);
  }

  api.renderPreview = renderPreview;

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
    let savedFilters;
    try {
      savedFilters = JSON.parse(dialog.dataset.saved || '[]');
    } catch (error) {
      savedFilters = [];
    }
    const base = dialog.dataset.base || '';
    const savedWrap = document.getElementById('range-saved');
    const savedList = document.getElementById('range-saved-list');

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
      toggle.setAttribute('aria-label', shown ? 'hide filters' : 'show filters');
      toggle.setAttribute('title', shown ? 'hide filters' : 'show filters');
      dialog.classList.toggle('panel-hidden', !shown);
    }

    function absoluteOptions(grain) {
      const groups = [];
      if (grain === 'month') {
        for (let y = firstYear; y <= lastYear; y += 1) {
          const group = el('optgroup', { label: String(y) });
          MONTH_SHORT.forEach((name, i) => {
            group.appendChild(el('option', { value: `${y}-${pad(i + 1)}`, text: `${name} ${y}`.toLowerCase() }));
          });
          groups.push(group);
        }
      } else if (grain === 'quarter') {
        for (let y = firstYear; y <= lastYear; y += 1) {
          const group = el('optgroup', { label: String(y) });
          [1, 2, 3, 4].forEach((q) => group.appendChild(el('option', { value: `${y}-Q${q}`, text: `q${q} ${y}` })));
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
      const name = which === 'start' ? 'start' : 'end';
      if (rule.grain === 'day') {
        const input = el('input', { type: 'date', className: 'range-date neu-field', 'aria-label': name, value });
        input.addEventListener('change', () => setAbsolute(which, input.value));
        return input;
      }
      const select = el('select', { className: 'range-select neu-field', 'aria-label': name }, absoluteOptions(rule.grain));
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
      const name = which === 'start' ? 'start' : 'end';
      const otherValue = Number(rule[which === 'start' ? 'end' : 'start']);
      const minimum = which === 'end' ? Math.max(-limit, otherValue) : -limit;
      const maximum = which === 'start' ? Math.min(limit, otherValue) : limit;
      const down = el('button', {
        type: 'button', className: 'range-step neu-press', 'aria-label': `${name} earlier`, disabled: value <= minimum, text: '−',
      });
      const up = el('button', {
        type: 'button', className: 'range-step neu-press', 'aria-label': `${name} later`, disabled: value >= maximum, text: '+',
      });
      down.addEventListener('click', () => setRelative(which, value - 1));
      up.addEventListener('click', () => setRelative(which, value + 1));
      const input = el('input', {
        type: 'text', inputmode: 'numeric', className: 'range-step-value neu-field', 'aria-label': `${name}, ${rule.grain}s from now`, value: signed(value),
      });
      const commit = () => {
        const typed = parseOffset(input.value);
        if (typed === null) {
          input.value = signed(value);
          return;
        }
        setRelative(which, typed, true);
      };
      input.addEventListener('change', commit);
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
          event.preventDefault(); // Enter sets the value; it doesn't Apply
          commit();
        }
      });
      return el('div', { className: 'range-stepper', role: 'group', 'aria-label': name }, [
        down,
        input,
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

    // Steppers can't cross the other end. A typed value can; the other end
    // moves to meet it, as it does for absolute values.
    function setRelative(which, value, typed) {
      const next = clampOffset(rule.grain, value);
      rule = { ...rule, [which]: next };
      if (typed && Number(rule.start) > Number(rule.end)) {
        rule = { ...rule, [which === 'start' ? 'end' : 'start']: next };
      }
      render();
      if (typed) {
        const slot = which === 'start' ? startSlot : endSlot;
        const field = slot.querySelector('.range-step-value');
        if (field) field.focus();
      }
    }

    // Saved filters: click to load (Apply still commits), delete in place.
    function renderSaved() {
      savedWrap.hidden = savedFilters.length === 0;
      savedList.replaceChildren(...savedFilters.map((item) => {
        const load = el('button', {
          type: 'button', className: 'range-saved-name neu-press', 'aria-pressed': sameRule(item.rule, rule) ? 'true' : 'false',
        }, [el('span', { text: item.name }), el('span', { className: 'range-saved-created', text: item.created })]);
        load.addEventListener('click', () => {
          rule = { ...item.rule };
          render();
        });
        const remove = el('button', {
          type: 'button', className: 'icon-button range-saved-delete', 'aria-label': `delete ${item.name}`, title: `delete ${item.name}`,
        }, [el('i', { 'data-lucide': 'x', className: 'menu-icon', 'aria-hidden': 'true' })]);
        remove.addEventListener('click', () => deleteSaved(item));
        return el('li', { className: 'range-saved-row' }, [load, remove]);
      }));
      if (window.lucide && typeof window.lucide.createIcons === 'function') window.lucide.createIcons();
    }

    async function saveCurrent() {
      const name = window.prompt('name this filter');
      if (name === null || !name.trim()) return;
      const body = new URLSearchParams({
        name, grain: rule.grain, mode: rule.mode, start: String(rule.start), end: String(rule.end),
      });
      try {
        const response = await fetch(`${base}/window/saved`, { method: 'POST', body });
        const data = await response.json();
        if (!response.ok) {
          window.alert((data.error || 'that filter could not be saved').toLowerCase());
          return;
        }
        savedFilters = [data, ...savedFilters];
        renderSaved();
      } catch (error) {
        window.alert('that filter could not be saved');
      }
    }

    async function deleteSaved(item) {
      if (!window.confirm(`delete "${item.name}"?`.toLowerCase())) return;
      try {
        const response = await fetch(`${base}/window/saved/${item.id}/delete`, { method: 'POST' });
        if (!response.ok && response.status !== 404) {
          window.alert('that filter could not be deleted');
          return;
        }
        savedFilters = savedFilters.filter((other) => other.id !== item.id);
        renderSaved();
      } catch (error) {
        window.alert('that filter could not be deleted');
      }
    }

    document.getElementById('range-save').addEventListener('click', saveCurrent);

    function render() {
      renderSaved();
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
