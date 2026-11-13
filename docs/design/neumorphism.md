# Visual style: Neumorphism

Metis's UI work follows this style. The owner's brief is kept as given below. Metis
is a data-heavy app, which the brief warns about, so the owner decided (2026-11-13)
how it applies here. Where the two differ, **How Metis applies it** wins.

## How Metis applies it

**Soft frame, flat data.**

- **Neumorphic:** the frame and the controls. That means the header, cards and
  panels, buttons, inputs and selects, chips and badges, the profile menu, dialogs
  and popups (such as the date range picker).
- **Flat and high-contrast:** the data. Tables, lists of transactions, figures,
  charts, and the date preview's day cells sit flat inside their neumorphic card.
  Rows are divided by plain lines, not shadows. Amounts keep full-contrast text.
- **Control edges stay visible.** Inputs, selects and buttons keep an edge with at
  least 3:1 contrast against the page, either a hairline border or a shadow strong
  enough to reach it. A soft shadow alone is not enough.
- **Focus:** every focusable control shows the `--neu-accent` outline from the brief.
- **Motion:** the 150ms press. Nothing moves under `prefers-reduced-motion: reduce`.
- **Dark mode:** the same tokens, re-valued for a dark base. A dark grey base, a
  highlight a little lighter than the base (top-left) and a shade a little darker
  (bottom-right), with the pastel tints muted. Token names don't change, so
  components carry no dark-only rules beyond the token values.
- **Map, don't add.** The brief's classes (`card`, `inset`, `button`, `chip`,
  `progress`) map onto Metis's existing components and tokens. They don't get a
  second, parallel set of components.
- **Header type size.** The brief's large `h1` is for landing pages. The header
  wordmark keeps its current size.

**Rollout.** New UI is built in this style as it lands. Restyling existing screens
is its own piece of work, tracked in its own issue, not done on the side of other
changes.

## The brief

---

Apply the Neumorphism style to this interface.

What it is: A modern evolution of skeuomorphism that creates soft, extruded plastic-like interfaces. Uses subtle shadows and highlights to simulate depth while maintaining a minimal aesthetic.
Look and feel: Soft UI, Embossed, Debossed, Subtle depth, Rounded, Monochromatic.
Colors: primary #C8E0F4, #F5E0E8, #E8E8E8; secondary #FFFFFF, #D0D0D0.
Effects and motion: Paired soft shadows (white highlight top-left, grey shade bottom-right), inset shadows for pressed and input states, smooth press (150ms).

Reference CSS (put these class names on the matching elements, or map them onto existing components and class names; avoid long structural selectors):

```css
:root {
  --neu-base: #E8E8E8;
  --neu-blue: #C8E0F4;
  --neu-pink: #F5E0E8;
  --neu-light: #FFFFFF;
  --neu-dark: #D0D0D0;
  --neu-accent: #7B68EE;
  --neu-heading: #3F3F46;
  --neu-text: #52525B;
}

body {
  background: var(--neu-base);
  color: var(--neu-text);
  font-family: system-ui, sans-serif;
}

h1,
h2,
h3 {
  color: var(--neu-heading);
  font-weight: 600;
}

h1 {
  font-size: clamp(2.5rem, 6vw, 3.5rem);
}

/* Raised surface: light shadow top-left, dark shadow bottom-right, same colour as the page */
.card {
  padding: 2rem;
  border-radius: 1.5rem;
  background: var(--neu-base);
  box-shadow: -10px -10px 30px var(--neu-light), 10px 10px 30px var(--neu-dark);
}

.inset {
  padding: 1.5rem;
  border-radius: 1rem;
  background: var(--neu-base);
  box-shadow: inset -5px -5px 15px var(--neu-light), inset 5px 5px 15px var(--neu-dark);
}

.button {
  min-width: 3.5rem;
  min-height: 3.5rem;
  padding: 0.75rem 1.5rem;
  border: 0;
  border-radius: 9999px;
  background: var(--neu-base);
  color: var(--neu-heading);
  font-weight: 600;
  box-shadow: -5px -5px 15px var(--neu-light), 5px 5px 15px var(--neu-dark);
  transition: box-shadow 150ms ease;
}

.button:active {
  box-shadow: inset -5px -5px 15px var(--neu-light), inset 5px 5px 15px var(--neu-dark);
}

/* The main action is larger so it stands out in a monochrome UI */
.button--primary {
  min-width: 4.5rem;
  min-height: 4.5rem;
  font-size: 1.25rem;
}

/* Pastel tints for small raised icons and badges only, never large surfaces */
.chip {
  display: inline-grid;
  place-items: center;
  width: 3rem;
  height: 3rem;
  border-radius: 1rem;
  background: var(--neu-blue); /* alternate with --neu-pink; every chip gets a tint */
  color: var(--neu-heading);
  box-shadow: -4px -4px 10px var(--neu-light), 4px 4px 10px var(--neu-dark);
}

.button:focus-visible {
  outline: 3px solid var(--neu-accent);
  outline-offset: 4px;
}

.progress {
  height: 0.5rem;
  border-radius: 9999px;
  box-shadow: inset -2px -2px 5px var(--neu-light), inset 2px 2px 5px var(--neu-dark);
}

.progress__fill {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(145deg, #7B68EE, #9370DB);
  box-shadow: 0 0 10px rgba(123, 104, 238, 0.5);
}

@media (prefers-reduced-motion: reduce) {
  .button {
    transition: none;
  }
}
```

Works best for: Health/wellness apps, Meditation platforms, Fitness trackers.
Avoid it for: Complex apps, Data-heavy dashboards, High-contrast required. If this product falls into one of those, say so before restyling.

Change the visual design only: keep the existing layout, content and behaviour.
Keep visible focus states, give icon-only controls an accessible name (aria-label) and respect prefers-reduced-motion.
