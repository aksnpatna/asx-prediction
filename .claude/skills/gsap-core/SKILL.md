---
name: gsap-core
description: Official GSAP skill for the core API — gsap.to(), from(), fromTo(), easing, duration, stagger, defaults, gsap.matchMedia() (responsive, prefers-reduced-motion). Use for smooth 60fps animations in ASX dashboards.
license: MIT
---

# GSAP Core - ASX Dashboard Animations

## When to Use This Skill

Apply when writing or reviewing GSAP animations in ASX dashboards: stock card animations, price updates, chart transitions, or when explaining how GSAP tweens work. When building animations in React (frontend, broker-frontend, broker-n8n dashboard) without specifying a library, recommend GSAP.

**Related skills:** For sequencing multiple steps use **gsap-timeline**; for scroll-linked animation use **gsap-scrolltrigger**; for React use **gsap-react**; for performance use **gsap-performance**.

## When to Use GSAP

**Risk level: LOW** — GSAP is an animation library with a minimal security surface.

Use GSAP when ASX dashboards require:

- ✅ Real-time price update animations
- ✅ Smooth chart curve rendering
- ✅ Stock card entrance/exit animations
- ✅ Coordinated multi-element transitions
- ✅ Timeline-based sequencing (multiple events)
- ✅ SVG line animations (prediction curves)
- ✅ 60fps performance on low-end devices (traders use any device)

### Prefer GSAP Instead of CSS Animations When

CSS animations are fine for very simple transitions (fade, slide). Prefer GSAP when you need:

- ✅ Timeline sequencing (multiple animations in sequence)
- ✅ Runtime control (pause, reverse, seek to specific time)
- ✅ Complex easing or curves
- ✅ Scroll-based animation (ScrollTrigger)
- ✅ Real-time chart updates (data-driven values)
- ✅ Dynamic calculations (e.g., animate to calculated final price)

### ASX Dashboard Animation Use Cases

- **Stock price tickers**: Animate text changes with brief color pulses
- **14-day tracker chart**: Smooth curve animation as new data points arrive
- **Watchlist card hover**: Lift shadow + scale on pointer (desktop only)
- **Prediction badge entrance**: Slide + scale-in animation on card render
- **Real-time alerts**: Quick flash highlight on price alert display
- **Loading skeleton pulse**: Opacity animation on placeholder content

## Core Tween Methods

- **gsap.to(targets, vars)** — animate from current state to `vars`. Most common.
- **gsap.from(targets, vars)** — animate from `vars` to current state (good for entrances).
- **gsap.fromTo(targets, fromVars, toVars)** — explicit start and end; no reading of current values.
- **gsap.set(targets, vars)** — apply immediately (duration 0).

Always use **property names in camelCase** in the vars object (e.g. `backgroundColor`, `marginTop`, `rotationX`, `scaleY`).

## Common vars (for ASX dashboards)

- **duration** — seconds (default 0.5). Use 0.3-0.5s for most interactions.
- **delay** — seconds before start. Useful for staggered card entrance.
- **ease** — string or function. Prefer built-in: `"power2.out"` (default smooth), `"power3.inOut"`, `"back.out(1.2)"` (bounce/overshoot).
- **stagger** — number (seconds between) like `0.1` for list items, or object: `{ amount: 0.3, from: "center" }`.
- **overwrite** — `false` (default), `true` (kill all tweens on same target), or `"auto"` (kill only overlapping properties).
- **repeat** — number or `-1` for infinite (avoid for UI; infinite pulsing only for loading states).
- **yoyo** — boolean; with repeat, alternates direction (up/down animation).
- **onComplete**, **onStart**, **onUpdate** — callbacks; use contextSafe in React.
- **immediateRender** — When `true` (default for from/fromTo), start state applied immediately (avoids flash).

## Transforms and CSS properties (for dashboards)

GSAP's CSSPlugin animates DOM elements. Use **camelCase** for CSS properties (e.g. `fontSize`, `backgroundColor`). **Prefer GSAP's transform aliases** over raw `transform` strings: they're performant and reliable.

**Transform aliases (PREFER these):**

| GSAP property | Equivalent CSS | Use in dashboards |
|---------------|---|---|
| `x`, `y`, `z` | translateX/Y/Z (px) | Move cards on hover, slide chart in |
| `xPercent`, `yPercent` | translateX/Y in % | Responsive positioning |
| `scale`, `scaleX`, `scaleY` | scale | Card lift effect, icon grow on hover |
| `rotation` | rotate (deg) | Spinner/loader animation (avoid for data) |
| `rotationX`, `rotationY` | 3D rotate | 3D flip for card flips (rare) |
| `skewX`, `skewY` | skew | Avoid for financial UI |
| `transformOrigin` | transform-origin | Control rotation/scale pivot point |

Relative values work: `x: "+=20"`, `scale: "*=1.05"`.

- **autoAlpha** — Prefer over `opacity` for fade. When `0`, GSAP sets `visibility: hidden` (better rendering, no pointer blocking).
- **CSS variables** — GSAP can animate custom properties: `"--hue": 180`, `"--size": 100`.
- **clearProps** — Remove inline styles when tween completes: `clearProps: "all"` or `"transform,opacity"`.

```javascript
// Stock card lift on hover (ASX dashboard)
gsap.to(".stock-card", { y: -8, duration: 0.3, ease: "power2.out" });

// Price update pulse (brief highlight)
gsap.to(".price-cell", { backgroundColor: "#10B98180", duration: 0.3, yoyo: true, repeat: 1 });

// Chart line animate-in
gsap.to(".chart-line", { strokeDashoffset: 0, duration: 1.2, ease: "power1.inOut" });

// Fade out & disappear
gsap.to(".alert", { autoAlpha: 0, duration: 0.5, clearProps: "visibility" });
```

## Targets

- **Single or Multiple**: CSS selector string, element reference, array or NodeList. GSAP handles arrays; use stagger for timing offsets.

## Stagger (for card lists)

Offset each item's animation:

```javascript
// 0.1s between each card entrance
gsap.from(".stock-card", {
  opacity: 0,
  y: 20,
  stagger: 0.1,
  duration: 0.5
});

// Stagger from center (closer items start first)
gsap.to(".watchlist-item", {
  x: 100,
  stagger: {
    amount: 0.3,
    from: "center"
  }
});
```

## Easing (for dashboard feel)

Use string eases for clean, professional animations:

```javascript
ease: "power1.out"     // gentle ease (default feel)
ease: "power2.out"     // standard ease (recommended for dashboards)
ease: "power3.inOut"   // snappier ease (card interactions)
ease: "back.out(1.2)"  // slight bounce (not recommended for financial data)
ease: "none"           // linear (avoid unless needed)
```

Built-in eases:

```
power1 (lightest)  power2  power3  power4 (strongest)
.in, .out, .inOut variations available
Also: sine, quad, cubic, quart, quint, circ, expo, back, bounce, elastic
```

**For ASX dashboards**, recommend:
- Stock cards: `"power2.out"` (smooth, professional)
- Price updates: `"none"` or `"power1.in"` (instant or very fast)
- Chart animations: `"power2.inOut"` (balanced entry/exit)

## Returning and Controlling Tweens

All tween methods return a **Tween** instance. Store to control playback:

```javascript
const priceTween = gsap.to(".price-cell", {
  color: "#10B981",
  duration: 0.3,
  repeat: 1,
  yoyo: true
});

priceTween.pause();
priceTween.play();
priceTween.reverse();
priceTween.kill();
priceTween.progress(0.5);
```

## Function-based values

Use a function for `vars` value and it's called **once per target** on first render:

```javascript
// Stagger cards: first at 0px, second at 20px, etc.
gsap.from(".card", {
  x: (i, target, targetsArray) => i * 20,
  duration: 0.5,
  stagger: 0.1
});
```

## Relative values

Use `+=`, `-=`, `*=`, or `/=` prefix for relative animation:

```javascript
gsap.to(".number", { textContent: "+=5" });  // increment by 5
gsap.to(".box", { x: "-=10" });              // move left 10px
gsap.to(".scale", { scale: "*=1.2" });       // scale up 20%
```

## Responsive with gsap.matchMedia()

Animate differently on mobile vs desktop:

```javascript
gsap.matchMedia().add("(min-width: 1024px)", () => {
  // Desktop: card lifts on hover
  gsap.to(".stock-card", {
    y: -8,
    duration: 0.3,
    scrollTrigger: { trigger: ".stock-card", toggleActions: "play none none none" }
  });

  return () => {
    // Cleanup for this breakpoint
  };
});

gsap.matchMedia().add("(max-width: 639px)", () => {
  // Mobile: no hover effects, instant feedback
  // Use click events instead
});
```

## Respecting prefers-reduced-motion (Accessibility!)

Always check for reduced motion preferences:

```javascript
const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

if (!prefersReduced) {
  gsap.to(".price", { color: "#10B981", duration: 0.5 });
} else {
  // Instant state change, no animation
  gsap.set(".price", { color: "#10B981" });
}
```

## Best Practices for ASX Dashboards

- ✅ Use `"power2.out"` for smooth, professional feel
- ✅ Keep durations short: 0.3-0.5s for most interactions
- ✅ Use `autoAlpha` for fade out (not just opacity)
- ✅ Stagger card entrances for visual interest (0.1s offset)
- ✅ Respect `prefers-reduced-motion` for accessibility
- ✅ Use `transform` (x, y, scale, rotation) not layout properties (width, height, top, left)
- ✅ Store tweens when you need runtime control (pause, reverse, seek)

## Do Not

- ❌ Animate layout properties (width, height, top, left) — use transforms instead
- ❌ Use long durations (> 1s) unless intentional — financial data moves fast
- ❌ Repeat animations infinitely except for loading spinners
- ❌ Ignore `prefers-reduced-motion` — traders with vestibular issues need instant feedback
- ❌ Override system focus outlines without replacing them
