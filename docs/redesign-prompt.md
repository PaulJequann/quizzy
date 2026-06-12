# Quizzy UI Redesign — Warm Paper & Ink

## Mission

Refine Quizzy's visual design into a calm, elegant, well-typeset study tool, and **unify the quiz-take page with the rest of the app**. Today the app has two competing identities:

1. Most pages: a light cream/paper editorial look (serif headings, stone palette, lime accent) — the right direction, but loud in places.
2. The take page ([app/templates/quizzes/take.html](app/templates/quizzes/take.html)): a dark gaming/terminal look — near-black `#080d13` background, neon lime/teal gradient progress bar, dark navy cards, olive "Next" button. **This gets removed entirely.**

Target voice: "beautifully typeset study tool" — think Readwise or a fine notebook, not a SaaS dashboard and not a game.

This direction was validated against two alternatives via a side-by-side prototype on 2026-06-12; "Warm Paper & Ink" won for its character (Fraunces headings), fit with the product, and continuity with the existing aesthetic.

## Hard constraints

- **Tailwind v4 browser CDN** (`@tailwindcss/browser@4` in base.html). There is no build step. `@theme` does **not** work in the separately linked stylesheet — define tokens as plain `:root { --color-*: ... }` custom properties in [app/static/css/app.css](app/static/css/app.css) and consume them via component classes or arbitrary-value utilities (`bg-[var(--color-bg)]`).
- **Light mode only.** No dark mode, no theme toggle. (Possible later follow-up.)
- **Keep the top header navigation.** No sidebar.
- **No hardcoded hex values in Jinja templates** — every color flows through a CSS variable or an existing component class.
- Keep the existing HTMX wiring exactly as is: publish is `hx-post` swapping `#status-badge` (outerHTML); add-question is `hx-post` swapping `#question-workspace` (outerHTML). Restyle the swapped partials, don't rewire them.
- Keep the take page's existing vanilla-JS step flow (`data-quiz-flow`, hidden fieldsets, Back/Next/Submit) — restyle only.

## Design tokens

Replace the current `:root` block in app.css with:

```css
:root {
  /* Backgrounds */
  --color-bg: #faf8f4;            /* page */
  --color-surface: #fffdf9;       /* cards, inputs */
  --color-surface-2: #f5f1ea;     /* hover fills, progress track, wells */

  /* Borders */
  --color-border: #e7e1d6;
  --color-border-strong: #cfc6b6;

  /* Text */
  --color-text: #2a2723;
  --color-text-2: #6b655b;        /* secondary / descriptions */
  --color-text-3: #a39b8d;        /* muted / hints / timestamps */

  /* Accent — evergreen */
  --color-accent: #0f766e;
  --color-accent-hover: #0d655e;
  --color-accent-subtle: #e8f3f1; /* selected fills, focus rings */
  --color-accent-text: #0b5953;   /* accent-colored text on light bg */

  /* Semantic */
  --color-success: #16a34a;
  --color-success-subtle: #f0fdf4;
  --color-error: #dc2626;
  --color-error-subtle: #fef2f2;
  --color-warning-text: #92400e;
  --color-warning-subtle: #fef3c7;

  /* Shadow — soft paper lift, never heavy */
  --shadow-card: 0 1px 2px rgba(42, 39, 35, 0.05), 0 4px 16px rgba(42, 39, 35, 0.04);
}
```

## Typography

Import from Bunny Fonts in base.html:

```html
<link rel="stylesheet" href="https://fonts.bunny.net/css?family=fraunces:500,600,700|source-sans-3:400,500,600&display=swap">
```

- **Headings & question stems**: `Fraunces, Georgia, serif`, weight 500–600. Replace the `ui-serif` stack and the `font-serif` utility usage with a `.font-display` class (or set it on heading elements in CSS).
- **Body & UI**: `Source Sans 3, system-ui, sans-serif`, 15.5px base, line-height 1.6, antialiased.
- **Kill the shouting**: today's UI leans on `font-weight: 900` + uppercase + wide letter-spacing everywhere (`.eyebrow`, `.field-label`, chips, buttons). Keep uppercase micro-labels only for `.eyebrow`-style kickers, at weight 600, color `--color-text-3` or `--color-accent-text`. Labels, chips, and buttons drop to weight 500–600, normal case.
- Scale: page titles ~2–2.4rem (down from the current 5xl/6xl heroes), card titles ~1.1rem, question stems ~1.35rem serif, metadata 0.8rem.

## Shape, spacing, motion

- Radius: `0.75rem` cards/choice tiles, `0.6rem` buttons/inputs, `0.4rem` badges/chips, pill only for the progress bar and radio dots.
- Card treatment: `--color-surface` fill, 1px `--color-border`, `--shadow-card`. **Remove** the `5px`/`6px` colored top borders on `.quiz-card`/`.score-card`, the left accent borders on `.question-preview`, and the hover "pop" (`translateY(-1px)` + `0 8px 0` hard shadow) on buttons. Card hover: border darkens to `--color-border-strong`, at most 1px lift.
- The `.shell-grain` grid overlay: remove it. Let the warm background do the work.
- Transitions: 120–150ms ease, on border-color/background/opacity. Nothing decorative.
- Focus: every interactive element gets a visible ring — `border-color: var(--color-accent); box-shadow: 0 0 0 3px var(--color-accent-subtle)`.

## Components

**Buttons** — `.btn-primary`: `--color-accent` fill, white text, hover `--color-accent-hover`. `.btn-secondary`: `--color-surface` fill, 1px `--color-border`, `--color-text-2` text; hover darkens border and text. Drop the 2px black borders and lime-on-black scheme. Disabled: opacity 0.45.

**Inputs** (`.field`, `.field-label`): surface fill, 1px border, 0.6rem radius, accent focus ring. Labels: 0.82rem, weight 600, `--color-text-2`, normal case.

**Status badges** ([_status_badge.html](app/templates/quizzes/_status_badge.html)): draft = `--color-warning-subtle` / `--color-warning-text`; published = `--color-success-subtle` / dark green. 0.4rem radius, weight 600, no 900-weight uppercase shouting.

**Choice tiles** (take page `.runner-option`, detail/forms `.radio-tile`/`.answer-option`): full-card clickable, surface fill, 1px border, 0.75rem radius. On the take page the leading affordance is the numbered keycap (see "Keyboard-first take page"); elsewhere use a custom radio dot (1.15rem circle; checked = accent fill + white inner dot). Checked card: `--color-accent-subtle` fill, accent border, `0 0 0 1px var(--color-accent)` ring. Hover: `--color-surface-2` fill.

## The Ink Ledger (progress motif)

Kill the progress *bar*. Progress is a **row of discrete ink marks, one per question** — the single visual motif connecting the take and results pages.

**Take page:** a row of N marks (~18×6px rounded rects, pill radius, 6px gap) beside the "Question N of M" count. Unanswered = transparent fill with 1px `--color-border` outline. Answered = `--color-accent` fill, with a ~150ms "ink soak" transition (opacity + slight scale). Current question's mark = slightly taller (~9px). For a 5-question drill, discrete marks are honest where a continuous bar is fake precision.

**Results page:** the same ledger reappears at the top of the score card — each mark inked `--color-success` or `--color-error` per answer, each an anchor link scrolling to that question's review card (`href="#q-{{ loop.index }}"`, review cards get matching ids).

Constraints:

- **Touch targets:** marks-as-links get a padded hit area (~44px via padding or pseudo-element) while the visible ink stays small.
- **Scale fallback:** allow `flex-wrap`; if `quiz.questions|length > 20`, render a plain flat accent bar instead (Jinja conditional). Seeded drills are ~5 questions so this rarely triggers, but it keeps the design honest.

## Keyboard-first take page

The take page must be fully drivable from the keyboard — this is a drill tool; repetition speed is the product.

**Keymap** (active only on the take page, never when an input/textarea/contenteditable has focus):

- `1`–`9` (and `a`–`d` as aliases for 1–4): select the Nth choice on the current question. Ignore keys beyond the choice count.
- `Enter`: advance — triggers whichever of Next/Submit is visible, only if the current question is answered (otherwise flash the hint text).
- `Backspace` or `←`: go back (same rules as the Back button).
- Guard against key-repeat (ignore `event.repeat`) and modifier combos (ignore if `ctrl/meta/alt`).

**Keycap affordance**: each choice tile's leading element is a small keycap showing its number — ~1.5rem square, `--color-surface-2` fill, 1px `--color-border`, 0.4rem radius, `--color-text-2` numeral with `font-variant-numeric: tabular-nums`. On the checked tile the keycap inverts to `--color-accent` fill with white numeral. The keycap **replaces** the radio dot as the visible leading affordance (the real radio input stays, visually hidden, for semantics and form submission).

Mouse/touch behavior is unchanged; keys are purely additive. On coarse pointers (`@media (pointer: coarse)`) the keycaps may be hidden — they're meaningless on touch.

## Per-file work

1. **[app/static/css/app.css](app/static/css/app.css)** — new tokens; restyle every component class; **delete the entire dark runner section** (`.quiz-runner` gradient, `.runner-topbar`, `.runner-progress` neon gradient, `.runner-option` navy styles, `.runner-quit`, etc.) and rebuild the `runner-*` classes on the light token system.
2. **[app/templates/base.html](app/templates/base.html)** — add font link; body uses `bg-[var(--color-bg)] text-[var(--color-text)]`; remove `.shell-grain` div; restyle header (keep layout: logo left, Quizzes / New quiz right; swap the black-square lime "Q" mark for something quieter, e.g. accent-colored serif "Q").
3. **[app/templates/quizzes/list.html](app/templates/quizzes/list.html)** — tame the hero (smaller serif title, weight 600); quiz cards per spec above; stats strip and empty state restyled on tokens.
4. **[app/templates/quizzes/detail.html](app/templates/quizzes/detail.html)** + **[question_form.html](app/templates/quizzes/question_form.html)** + partials **[_question_workspace.html](app/templates/quizzes/_question_workspace.html)**, **[_question_form_inner.html](app/templates/quizzes/_question_form_inner.html)** — same card/input/badge treatment. Partials must look identical before and after an HTMX swap.
5. **[app/templates/quizzes/new.html](app/templates/quizzes/new.html)** — form styling per input spec.
6. **[app/templates/quizzes/take.html](app/templates/quizzes/take.html)** — change `body_class` from `bg-[#080d13]` to the standard light background. Keep the focused layout (no site header), `max-w-2xl` centered column. Header: small uppercase kicker (accent text) + serif title + quiet "Quit" ghost link (no "x Quit" terminal styling). Progress: "Question N of M" count + the Ink Ledger (see motif spec) — no bar, no gradient. Stem in serif at ~1.35rem, no boxed dark card. Choice tiles and Back/Next/Submit buttons per component spec; hint text in `--color-text-3`. Extend the existing step-flow script with the keymap above (~20 lines; same IIFE, no new dependencies).
7. **[app/templates/attempts/results.html](app/templates/attempts/results.html)** — score card: Ink Ledger (success/error inks, anchor links) above a large serif percentage, letter grade as accent-subtle badge (not black/lime block), pass/fail as success/warning badge. Per-question cards get `id="q-{{ loop.index }}"` and keep the correct/incorrect left-border idea but use `--color-success`/`--color-error` at 3px, with `badge-correct`/`badge-incorrect` on the semantic subtle/text pairs. Explanation wells: `--color-surface-2`.

## Anti-patterns (all present in the current code — remove on sight)

- ❌ `#080d13` body, navy `rgba(15, 23, 42, …)` cards, `#263248` borders (take page)
- ❌ Neon `linear-gradient(90deg, #bef264, #2dd4bf, #60a5fa)` progress bar
- ❌ Lime `#bef264` as an accent anywhere
- ❌ `font-weight: 900` + uppercase + letter-spacing as the default label voice
- ❌ Thick black borders / black fills with lime text (buttons, logo, grade badge)
- ❌ 5–6px decorative colored top borders on cards
- ❌ Hard offset hover shadows (`0 8px 0 …`) and translateY button pop
- ❌ `.shell-grain` background grid
- ❌ "x Quit" terminal-style affordances

## Acceptance checklist

- [ ] Take page is light, calm, and visually consistent with the rest of the app
- [ ] Fraunces renders for headings and question stems; Source Sans 3 for body/UI
- [ ] Choice tiles show an unmistakable selected state (fill + border + keycap), keycap at leading edge
- [ ] Keyboard flow works end-to-end: `1`–`4` selects, `Enter` advances, `Backspace`/`←` goes back; keys ignored while typing in form fields
- [ ] Ink Ledger fills as questions are answered on the take page; reappears on results with correct/incorrect inks and working anchor links
- [ ] All form inputs and buttons show a visible focus ring
- [ ] Status badge and question-workspace partials look correct after HTMX swaps (publish a quiz; add a question)
- [ ] No hex values remain in any Jinja template
- [ ] No 900-weight uppercase labels outside small kickers
- [ ] Results page reads clearly: score, grade, per-question correct/incorrect at a glance
- [ ] Quiz flow still works end-to-end: list → take → submit → results
