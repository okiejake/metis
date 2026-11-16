// The split lines in the edit form (templates/_tx_edit.html), wherever the form
// sits: in the ledger's dialog or on its own page.
//
// - The form has one layout. The first line's amount is the rest of the
//   transaction, so it can't be edited until there is a split.
// - The first "add split" adds a line at half the transaction; the original
//   line keeps the rest, so a split starts as two lines, half and half. Each
//   later one adds a blank line.
// - The lines always add up to the transaction. Changing a line's amount
//   changes the original's; changing the original's changes the final line's.
// - Each added line can be taken off. With none left, the transaction is no
//   longer split. "un-split" takes them all off.
// - Picking a bill on a line sets the line's category to the bill's.
// - Save waits until the split is valid, and the notice beside it says why.
//
// window.metisSplit.init(root) readies the forms inside root; tx-edit.js calls
// it after it fills the dialog. Without script the form still posts, and the
// server checks the lines.
(() => {
  // Amounts are worked in cents, so they add up exactly.
  function cents(text) {
    const value = parseFloat(text);
    return Number.isNaN(value) ? 0 : Math.round(value * 100);
  }

  function money(amount) {
    return (amount / 100).toFixed(2);
  }

  // amounts: [original, ...lines]. The line at `edited` changed; the original
  // takes the difference, unless it is the one that changed, when the final
  // line does.
  function balance(total, amounts, edited) {
    const out = amounts.slice();
    if (out.length < 2) { return out; }
    const absorb = edited === 0 ? out.length - 1 : 0;
    const others = out.reduce((sum, value, index) => (index === absorb ? sum : sum + value), 0);
    out[absorb] = total - others;
    return out;
  }

  // Why the lines can't be saved yet, or '' when they can. `chosen` says, for
  // each added line, whether it has a category.
  function problem(total, amounts, chosen) {
    if (amounts.some((value) => value > total || value < 0)) { return 'a line is more than the transaction'; }
    if (amounts.slice(1).some((value) => value <= 0)) { return 'every line needs an amount'; }
    if (chosen.some((has) => !has)) { return 'choose a category for each line'; }
    return '';
  }

  const api = { cents, money, balance, problem };
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
    return;
  }

  const formOf = (el) => el.closest('[data-split-form]');
  const linesOf = (form) => Array.from(form.querySelectorAll('[data-split-original], [data-split-line]'));
  const amountOf = (line) => line.querySelector('[data-split-amount]');
  const totalOf = (form) => cents(form.dataset.total);

  function sync(select) {
    select.dispatchEvent(new Event('picker:sync'));
  }

  function check(form) {
    const notice = form.querySelector('[data-split-notice]');
    const save = form.querySelector('[data-split-save]');
    let reason = '';
    if (form.hasAttribute('data-split-open')) {
      const lines = linesOf(form);
      const amounts = lines.map((line) => cents(amountOf(line).value));
      const chosen = lines.slice(1).map((line) => Boolean(line.querySelector('select[name="part_category"]').value));
      reason = problem(totalOf(form), amounts, chosen);
    }
    if (notice) { notice.textContent = reason; }
    if (save) { save.disabled = Boolean(reason); }
  }

  // The original keeps what the added lines don't.
  function settle(form) {
    const lines = linesOf(form);
    const taken = lines.slice(1).reduce((sum, line) => sum + cents(amountOf(line).value), 0);
    amountOf(lines[0]).value = money(totalOf(form) - taken);
  }

  function addLine(form, templateName) {
    const template = form.querySelector(`template[${templateName}]`);
    const rows = form.querySelector('[data-split-rows]');
    const line = template.content.firstElementChild.cloneNode(true);
    rows.appendChild(line);
    if (window.metisPicker) { window.metisPicker.init(line); }
    if (window.lucide && typeof window.lucide.createIcons === 'function') { window.lucide.createIcons(); }
    return line;
  }

  // Split or not: the first line's amount is editable only while split.
  function mark(form, open) {
    form.toggleAttribute('data-split-open', open);
    amountOf(linesOf(form)[0]).readOnly = !open;
  }

  function close(form) {
    form.querySelectorAll('[data-split-line]').forEach((line) => line.remove());
    mark(form, false);
    settle(form);
  }

  document.addEventListener('click', (event) => {
    const form = formOf(event.target);
    if (!form) { return; }
    if (event.target.closest('[data-split-add]')) {
      event.preventDefault();
      const first = !form.hasAttribute('data-split-open');
      mark(form, true);
      const line = addLine(form, first ? 'data-split-first' : 'data-split-blank');
      if (first) { settle(form); }
      check(form);
      const input = line.querySelector('.picker-input') || amountOf(line);
      if (input) { input.focus(); }
    } else if (event.target.closest('[data-split-remove]')) {
      event.target.closest('[data-split-line]').remove();
      if (!form.querySelector('[data-split-line]')) { close(form); } else { settle(form); }
      check(form);
    } else if (event.target.closest('[data-split-off]')) {
      event.preventDefault();
      close(form);
      check(form);
    }
  });

  document.addEventListener('input', (event) => {
    const input = event.target.closest('[data-split-amount]');
    const form = input && formOf(input);
    if (!form) { return; }
    const lines = linesOf(form);
    const edited = lines.indexOf(input.closest('[data-split-original], [data-split-line]'));
    const amounts = balance(totalOf(form), lines.map((line) => cents(amountOf(line).value)), edited);
    lines.forEach((line, index) => {
      if (index !== edited) { amountOf(line).value = money(amounts[index]); }
    });
    check(form);
  });

  document.addEventListener('change', (event) => {
    const form = formOf(event.target);
    if (!form) { return; }
    const bill = event.target.closest('select[data-bill-select]');
    if (bill) {
      const category = bill.selectedOptions[0] && bill.selectedOptions[0].dataset.category;
      const line = bill.closest('[data-split-original], [data-split-line]');
      const picker = line && line.querySelector('select[data-category-picker]');
      if (category && picker) {
        picker.value = category;
        sync(picker);
      }
    }
    check(form);
  });

  function init(root) {
    (root || document).querySelectorAll('[data-split-form]').forEach(check);
  }

  window.metisSplit = { init };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => init(document));
  } else {
    init(document);
  }
})();
