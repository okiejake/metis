// Text that doesn't fit fades out instead of wrapping (#123). Every .fade box
// keeps to one line; this marks the ones whose text is cut off with
// data-clipped, and only those fade, so short text is never dimmed.
//
// It runs when the page loads, when the window changes size, and when a
// script puts new content on the page ("metis:content" on document).
(() => {
  function mark(root) {
    (root || document).querySelectorAll('.fade').forEach((box) => {
      box.toggleAttribute('data-clipped', box.scrollWidth > box.clientWidth + 1);
    });
  }

  let pending = 0;
  function soon() {
    cancelAnimationFrame(pending);
    pending = requestAnimationFrame(() => mark(document));
  }

  window.addEventListener('resize', soon);
  document.addEventListener('metis:content', (event) => mark(event.detail || document));
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', soon);
  } else {
    soon();
  }
  if (document.fonts && document.fonts.ready) { document.fonts.ready.then(soon); }
})();
