// The expected form: show only the fields that apply to what "repeats" says.
(() => {
  document.querySelectorAll('[data-expected-form]').forEach((form) => {
    const repeats = form.querySelector('[data-repeats]');
    if (!repeats) { return; }
    const sync = () => {
      const value = repeats.value;
      const oneTime = value === 'one-time';
      form.querySelectorAll('[data-repeating]').forEach((el) => el.classList.toggle('hidden', oneTime));
      form.querySelectorAll('[data-one-time]').forEach((el) => el.classList.toggle('hidden', !oneTime));
      form.querySelectorAll('[data-repeats-only]').forEach((el) => {
        el.classList.toggle('hidden', el.dataset.repeatsOnly !== value);
      });
    };
    repeats.addEventListener('change', sync);
    sync();
  });
})();
