# Visual style: Neumorphism

Metis's UI work follows this style. It is the owner's brief, kept as given.

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
