// The ledger's edit dialog (#123). A link with data-edit-href opens that
// transaction's edit form over the table, without touching the table.
//
// Saving posts in the background. When the server answers with step 2, the
// rule, the dialog shows it while the table behind it is brought up to date;
// otherwise the fresh table takes the old one's place and the dialog closes.
// Cancel, skip, Escape or a click outside the form closes it.
//
// Without script every edit link is a page with the same form.
(() => {
  const dialogEl = () => document.getElementById('tx-edit-dialog');
  const sectionEl = () => document.querySelector('[data-section]');

  function fragment(html, selector) {
    const holder = document.createElement('div');
    holder.innerHTML = html;
    return holder.querySelector(selector);
  }

  function show(form) {
    const dialog = dialogEl();
    dialog.replaceChildren(form);
    if (window.metisPicker) { window.metisPicker.init(dialog); }
    if (window.metisSplit) { window.metisSplit.init(dialog); }
    if (window.lucide && typeof window.lucide.createIcons === 'function') { window.lucide.createIcons(); }
    if (!dialog.open) { dialog.showModal(); }
    document.dispatchEvent(new CustomEvent('metis:content', { detail: dialog }));
    // The form opens on its header; step 2 opens on the rule's pattern.
    const first = dialog.querySelector('[data-rule-prompt] input[name="pattern"], [autofocus]');
    if (first) { first.focus(); }
  }

  async function load(url) {
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) { throw new Error(`status ${response.status}`); }
    const form = fragment(await response.text(), '[data-tx-edit]');
    if (!form) { throw new Error('no form in the response'); }
    show(form);
  }

  // The table, fetched fresh behind the dialog. No dimming: the dialog covers it.
  async function refresh(section) {
    const response = await fetch(window.metisSections.nextOf(section), { credentials: 'same-origin' });
    if (response.ok) { window.metisSections.swap(section, await response.text()); }
  }

  function close() {
    const dialog = dialogEl();
    if (dialog && dialog.open) { dialog.close(); }
  }

  document.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) { return; }
    const dialog = dialogEl();
    if (!dialog || !dialog.showModal) { return; }
    const opener = event.target.closest('a[data-edit-href]');
    if (opener) {
      event.preventDefault();
      load(opener.dataset.editHref).catch(() => { window.location.href = opener.href; });
      return;
    }
    if (!dialog.contains(event.target)) { return; }
    if (event.target === dialog) {
      // The dialog itself is the target both on its padding and on the
      // backdrop; only a click outside its box closes it.
      const box = dialog.getBoundingClientRect();
      const inside = event.clientX >= box.left && event.clientX <= box.right
        && event.clientY >= box.top && event.clientY <= box.bottom;
      if (!inside) { close(); }
      return;
    }
    if (event.target.closest('[data-tx-edit-close], [data-rule-prompt] a[data-partial-href]')) {
      event.preventDefault();
      close();
    }
  });

  document.addEventListener('submit', async (event) => {
    const form = event.target;
    const dialog = dialogEl();
    const section = sectionEl();
    if (!(form instanceof HTMLFormElement) || !dialog || !dialog.contains(form) || !section || !window.metisSections) {
      return;
    }
    event.preventDefault();
    const data = new FormData(form, event.submitter || undefined);
    data.set('next', window.metisSections.nextOf(section));
    try {
      const response = await fetch(form.action, { method: 'POST', body: data, credentials: 'same-origin' });
      if (!response.ok) { throw new Error(`status ${response.status}`); }
      const html = await response.text();
      const step = fragment(html, '[data-tx-edit]');
      if (step) {
        show(step);
        await refresh(section);
        return;
      }
      window.metisSections.swap(section, html);
      close();
    } catch (error) {
      form.submit();
    }
  });
})();
