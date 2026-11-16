// The one category picker (#27). Every <select data-category-picker> becomes a
// type-ahead box; the select stays in the form, hidden, and carries the value,
// so the page works the same without script.
//
// - Typing narrows the list. Sub-categories stay under their parent.
// - When nothing matches exactly, the list offers to add what was typed: the
//   select is set to "new" and its form's new_category_name to the text.
// - With data-picker-submit (a list row) choosing submits the form; otherwise
//   choosing only sets the field.
//
// window.metisPicker.init(root) wires the pickers inside root; partial.js calls
// it after it replaces a section. A script that sets the select's value fires
// "picker:sync" on it, and the box shows the new choice.
(() => {
  let nextId = 0;

  function itemsOf(select) {
    const items = [];
    Array.from(select.children).forEach((node) => {
      if (node.tagName === 'OPTGROUP') {
        items.push({ group: node.label });
        Array.from(node.children).forEach((option, index) => {
          items.push({ value: option.value, text: option.textContent.trim(), parent: node.label, child: index > 0 });
        });
      } else if (node.value !== 'new') {
        items.push({ value: node.value, text: node.textContent.trim() });
      }
    });
    return items;
  }

  function visible(items, query) {
    const q = query.trim().toLowerCase();
    if (!q) { return items; }
    const shown = [];
    let pendingGroup = null;
    items.forEach((item) => {
      if (item.group !== undefined) {
        pendingGroup = item;
        return;
      }
      if (item.value === '') { return; }
      const hit = item.text.toLowerCase().includes(q) || (item.parent || '').toLowerCase().includes(q);
      if (!hit) { return; }
      if (item.parent && pendingGroup && pendingGroup.group === item.parent) {
        shown.push(pendingGroup);
        pendingGroup = null;
      }
      shown.push(item);
    });
    return shown;
  }

  function wire(select) {
    if (select.dataset.pickerReady) { return; }
    select.dataset.pickerReady = '1';
    const form = select.form;
    const canAdd = Boolean(select.querySelector('option[value="new"]'));
    const nameField = canAdd && form ? form.querySelector('[data-new-category]') : null;
    const submits = select.hasAttribute('data-picker-submit');
    const listId = `picker-list-${nextId += 1}`;

    const box = document.createElement('div');
    box.className = 'picker';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'picker-input';
    input.autocomplete = 'off';
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', listId);
    input.setAttribute('aria-label', select.getAttribute('aria-label') || 'category');
    input.placeholder = 'category';
    const list = document.createElement('ul');
    list.className = 'picker-list hidden';
    list.id = listId;
    list.setAttribute('role', 'listbox');
    box.append(input, list);
    select.classList.add('picker-native');
    select.tabIndex = -1;
    select.setAttribute('aria-hidden', 'true');
    select.after(box);
    if (nameField) { nameField.classList.add('hidden'); }

    const items = itemsOf(select);
    let shown = [];
    let active = -1;

    function currentText() {
      if (select.value === 'new' && nameField) { return nameField.value; }
      const option = select.selectedOptions[0];
      return option && option.value ? option.textContent.trim() : '';
    }

    function render() {
      list.innerHTML = '';
      const query = input.value;
      shown = visible(items, query).slice();
      const exact = items.some((item) => item.text && item.text.toLowerCase() === query.trim().toLowerCase());
      if (canAdd && query.trim() && !exact) {
        shown.push({ value: 'new', text: `add “${query.trim()}”`, adding: query.trim() });
      }
      let optionIndex = 0;
      shown.forEach((item, index) => {
        const li = document.createElement('li');
        if (item.group !== undefined) {
          li.className = 'picker-group';
          li.setAttribute('role', 'presentation');
          li.textContent = item.group;
        } else {
          li.className = `picker-option${item.child ? ' picker-child' : ''}${item.adding ? ' picker-add' : ''}`;
          li.setAttribute('role', 'option');
          li.id = `${listId}-${optionIndex += 1}`;
          li.dataset.index = String(index);
          li.textContent = item.text;
          li.setAttribute('aria-selected', index === active ? 'true' : 'false');
          if (index === active) {
            li.classList.add('is-active');
            input.setAttribute('aria-activedescendant', li.id);
          }
        }
        list.appendChild(li);
      });
      if (!shown.some((item, index) => index === active && item.group === undefined)) {
        input.removeAttribute('aria-activedescendant');
      }
    }

    function open() {
      list.classList.remove('hidden');
      input.setAttribute('aria-expanded', 'true');
      render();
    }

    function close(restore) {
      list.classList.add('hidden');
      input.setAttribute('aria-expanded', 'false');
      active = -1;
      if (restore) { input.value = currentText(); }
    }

    function move(step) {
      const choices = shown.map((item, index) => (item.group === undefined ? index : -1)).filter((i) => i >= 0);
      if (!choices.length) { return; }
      const at = choices.indexOf(active);
      active = choices[(at + step + choices.length) % choices.length];
      render();
    }

    function choose(item) {
      if (!item || item.group !== undefined) { return; }
      if (item.adding !== undefined) {
        select.value = 'new';
        if (nameField) { nameField.value = item.adding; }
        input.value = item.adding;
      } else {
        select.value = item.value;
        if (nameField) { nameField.value = ''; }
        input.value = item.value ? item.text : '';
      }
      close(false);
      select.dispatchEvent(new Event('change', { bubbles: true }));
      if (submits && form && select.value !== '') {
        if (typeof form.requestSubmit === 'function') { form.requestSubmit(); } else { form.submit(); }
      }
    }

    input.value = currentText();
    select.addEventListener('picker:sync', () => { input.value = currentText(); });
    input.addEventListener('focus', () => { input.select(); open(); });
    input.addEventListener('input', () => { active = -1; open(); move(1); });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') { event.preventDefault(); if (list.classList.contains('hidden')) { open(); } move(1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1); }
      else if (event.key === 'Enter') {
        if (!list.classList.contains('hidden')) {
          event.preventDefault();
          choose(shown[active]);
        }
      } else if (event.key === 'Escape') {
        // An open list closes first; the dialog around it stays open.
        if (!list.classList.contains('hidden')) { event.preventDefault(); }
        close(true);
      }
      else if (event.key === 'Tab') { close(true); }
    });
    list.addEventListener('mousedown', (event) => {
      const li = event.target.closest('.picker-option');
      if (!li) { return; }
      event.preventDefault();
      choose(shown[Number(li.dataset.index)]);
    });
    input.addEventListener('blur', () => close(true));
  }

  function init(root) {
    (root || document).querySelectorAll('select[data-category-picker]').forEach(wire);
  }

  window.metisPicker = { init };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => init(document));
  } else {
    init(document);
  }
})();
