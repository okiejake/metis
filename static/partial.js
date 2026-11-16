// Background actions for a page section (budget, the ledger, the inbox).
//
// A section is an element with data-section, data-section-next (its address
// on this site, for a form's next) and data-section-url (the same, to fetch),
// and data-state-query (what the server rendered it with). Inside it:
//
// - form[data-partial] posts in the background with next set to the
//   section's address and its current state, and the server's fresh copy
//   replaces the section, so totals are always right and the page keeps its
//   place. If anything goes wrong the form is submitted the ordinary way.
// - a[data-partial-href] fetches that address and replaces the section.
// - [data-toggle-row] opens or closes its [data-row]; a row whose list was not
//   rendered ([data-lazy]) is fetched open. [data-expand-all] and
//   [data-collapse-all] do every row.
// - [data-status-filter] shows only the [data-status] items of that status.
// - [data-reveal="id"] shows or hides the element with that id.
// - select[data-auto-submit] submits its form when chosen.
// - form[data-rule-prompt] keeps its count current as its fields change.
//
// Without script every one of these is an ordinary form or link.
(() => {
  const sectionOf = (el) => el.closest('[data-section]');

  function state(section, changes = {}) {
    const params = new URLSearchParams(section.dataset.stateQuery || '');
    const rows = Array.from(section.querySelectorAll('[data-row]'));
    if (rows.length) {
      const open = rows.filter((row) => row.classList.contains('is-open')).map((row) => row.dataset.row);
      if (open.length) { params.set('open', open.join(',')); } else { params.delete('open'); }
    }
    const status = section.querySelector('[data-status-filter].active');
    if (status) { params.set('status', status.dataset.statusFilter); }
    Object.entries(changes).forEach(([key, value]) => params.set(key, value));
    ['msg', 'err', 'undo', 'rule_tx', 'rule_kind'].forEach((key) => params.delete(key));
    return params.toString();
  }

  async function replace(section, response) {
    if (!response.ok) { throw new Error(`status ${response.status}`); }
    return swap(section, await response.text());
  }

  // Put the section in `html` in place of `section`.
  function swap(section, html) {
    const holder = document.createElement('div');
    holder.innerHTML = html;
    const fresh = holder.querySelector('[data-section]');
    if (!fresh || fresh.id !== section.id) { throw new Error('no section in the response'); }
    document.querySelectorAll('main > .flash').forEach((flash) => flash.remove());
    section.replaceWith(fresh);
    if (window.metisPicker) { window.metisPicker.init(fresh); }
    if (window.lucide && typeof window.lucide.createIcons === 'function') { window.lucide.createIcons(); }
    document.dispatchEvent(new CustomEvent('metis:content', { detail: fresh }));
    const focusable = fresh.querySelector('[data-rule-prompt] input[name="pattern"]');
    if (focusable) { focusable.focus(); }
    return fresh;
  }

  // A form's next: the section's address and its current state.
  function nextOf(section) {
    return `${section.dataset.sectionNext}?${state(section)}`;
  }

  function refresh(section, changes) {
    const url = `${section.dataset.sectionUrl}?${state(section, changes)}`;
    section.classList.add('is-busy');
    return fetch(url, { credentials: 'same-origin' })
      .then((response) => replace(section, response))
      .catch(() => { window.location.href = url.replace(section.dataset.sectionUrl, window.location.pathname); });
  }

  document.addEventListener('submit', async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-partial')) { return; }
    const section = sectionOf(form);
    if (!section) { return; }
    event.preventDefault();
    const data = new FormData(form, event.submitter || undefined);
    data.set('next', nextOf(section));
    section.classList.add('is-busy');
    try {
      await replace(section, await fetch(form.action, { method: 'POST', body: data, credentials: 'same-origin' }));
    } catch (error) {
      form.removeAttribute('data-partial');
      form.submit();
    }
  });

  document.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) { return; }
    const target = event.target;

    const link = target.closest('a[data-partial-href]');
    if (link && sectionOf(link)) {
      event.preventDefault();
      const section = sectionOf(link);
      section.classList.add('is-busy');
      fetch(link.dataset.partialHref, { credentials: 'same-origin' })
        .then((response) => replace(section, response))
        .catch(() => { window.location.href = link.href; });
      return;
    }

    const toggle = target.closest('[data-toggle-row]');
    if (toggle && sectionOf(toggle)) {
      event.preventDefault();
      const row = toggle.closest('[data-row]');
      const opening = !row.classList.contains('is-open');
      setOpen(row, opening);
      if (opening && row.querySelector(':scope > [data-row-detail] > [data-unsettled] [data-lazy]')) {
        refresh(sectionOf(row));
      }
      return;
    }

    const all = target.closest('[data-expand-all], [data-collapse-all]');
    if (all && sectionOf(all)) {
      const section = sectionOf(all);
      const opening = all.hasAttribute('data-expand-all');
      section.querySelectorAll('[data-row]').forEach((row) => setOpen(row, opening));
      if (opening && section.querySelector('[data-lazy]')) { refresh(section); }
      return;
    }

    const filter = target.closest('[data-status-filter]');
    if (filter && sectionOf(filter)) {
      event.preventDefault();
      const section = sectionOf(filter);
      const status = filter.dataset.statusFilter;
      section.querySelectorAll('[data-status-filter]').forEach((el) => el.classList.toggle('active', el === filter));
      section.querySelectorAll('[data-status]').forEach((item) => {
        item.classList.toggle('hidden', status !== 'all' && item.dataset.status !== status);
      });
      return;
    }

    const reveal = target.closest('[data-reveal]');
    if (reveal) {
      const shown = document.getElementById(reveal.dataset.reveal);
      if (!shown) { return; }
      const showing = !shown.classList.toggle('hidden');
      reveal.setAttribute('aria-expanded', String(showing));
      const field = shown.querySelector('input:not([type="hidden"])');
      if (showing && field) { field.focus(); }
    }
  });

  // For the ledger's edit dialog (tx-edit.js), which sits outside the section.
  window.metisSections = { swap, nextOf };

  function setOpen(row, open) {
    row.classList.toggle('is-open', open);
    const detail = row.querySelector(':scope > [data-row-detail]');
    if (detail) { detail.classList.toggle('hidden', !open); }
    const toggle = row.querySelector(':scope > .budget-bar-head [data-toggle-row]');
    if (toggle) {
      toggle.setAttribute('aria-expanded', String(open));
      toggle.classList.toggle('is-open', open);
    }
  }

  document.addEventListener('change', (event) => {
    const field = event.target.closest('select[data-auto-submit]');
    if (field && field.value && field.form) {
      if (typeof field.form.requestSubmit === 'function') { field.form.requestSubmit(); } else { field.form.submit(); }
    }
  });

  // The rule prompt's count follows its fields. POST, so the pattern and
  // amounts never sit in an address.
  let pending = null;
  document.addEventListener('input', (event) => {
    const form = event.target.closest('form[data-rule-prompt]');
    if (!form) { return; }
    clearTimeout(pending);
    pending = setTimeout(() => {
      const data = new FormData(form);
      data.delete('next');
      fetch(form.dataset.previewUrl, { method: 'POST', body: data, credentials: 'same-origin' })
        .then((response) => (response.ok ? response.json() : null))
        .then((result) => {
          const count = form.querySelector('[data-rule-count]');
          if (result && count) { count.textContent = String(result.count); }
        })
        .catch(() => {});
    }, 250);
  });
})();
