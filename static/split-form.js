// The shared split form (templates/_parts.html split_form), wherever it sits:
// on the split page or in a ledger row's panel swapped in by partial.js.
// "add another part" copies the last row, and the remainder follows the amounts.
// Delegated from the document, so a form added later needs nothing wired up.
(() => {
  const formOf = (el) => el.closest('[data-split-form]');

  function recalc(form) {
    const total = parseFloat(form.dataset.total) || 0;
    let allocated = 0;
    form.querySelectorAll('input[name="amount"]').forEach((input) => {
      const value = parseFloat(input.value);
      if (!Number.isNaN(value) && value > 0) { allocated += value; }
    });
    const remainder = total - allocated;
    const shown = form.querySelector('[data-split-remainder]');
    if (shown) {
      shown.textContent = `$${remainder.toFixed(2)}`;
      shown.classList.toggle('negative', remainder < -0.005);
    }
  }

  document.addEventListener('click', (event) => {
    const add = event.target.closest('[data-split-add]');
    const form = add && formOf(add);
    if (!form) { return; }
    const rows = form.querySelector('[data-split-rows]');
    const clone = rows.querySelector('.split-row:last-child').cloneNode(true);
    clone.querySelectorAll('input').forEach((input) => { input.value = ''; });
    clone.querySelector('select').selectedIndex = 0;
    rows.appendChild(clone);
    recalc(form);
  });

  document.addEventListener('input', (event) => {
    const form = formOf(event.target);
    if (form) { recalc(form); }
  });
})();
