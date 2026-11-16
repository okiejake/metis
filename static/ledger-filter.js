// The ledger's filter (templates/_parts.html filter_dialog). Without script it
// is an ordinary form: open it, pick, apply. With script:
// - the category search narrows the list as you type;
// - "all" stays apart from the other picks: choosing it clears them, and
//   choosing any other clears it (none left means all again);
// - cancel closes the filter without reloading the page.
(() => {
  document.addEventListener('input', (event) => {
    const search = event.target.closest('[data-category-search]');
    if (!search) { return; }
    const words = search.value.trim().toLowerCase();
    const list = search.closest('form').querySelector('[data-category-list]');
    list.querySelectorAll('[data-category-name]').forEach((item) => {
      item.hidden = Boolean(words) && !item.dataset.categoryName.includes(words);
    });
  });

  document.addEventListener('change', (event) => {
    const box = event.target.closest('[data-category-list] input[type="checkbox"]');
    if (!box) { return; }
    const list = box.closest('[data-category-list]');
    const all = list.querySelector('[data-category-all]');
    const picks = Array.from(list.querySelectorAll('input[type="checkbox"]')).filter((input) => input !== all);
    if (box === all) {
      if (all.checked) { picks.forEach((input) => { input.checked = false; }); }
    } else if (box.checked) {
      all.checked = false;
    }
    if (!picks.some((input) => input.checked)) { all.checked = true; }
  });

  document.addEventListener('click', (event) => {
    const cancel = event.target.closest('[data-filter-cancel]');
    if (!cancel) { return; }
    const popover = cancel.closest('details');
    if (!popover) { return; }
    event.preventDefault();
    cancel.closest('form').reset();
    popover.removeAttribute('open');
  });
})();
