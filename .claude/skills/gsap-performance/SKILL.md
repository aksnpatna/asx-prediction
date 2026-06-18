---
name: gsap-performance
description: Official GSAP skill for performance — prefer transforms, avoid layout thrashing, will-change, batching. Optimize ASX dashboard animations for 60fps.
license: MIT
---

# GSAP Performance - ASX Dashboard Optimization

## When to Use This Skill

Apply when optimizing GSAP animations for smooth 60fps in ASX dashboards, reducing jank, or when the user asks about animation performance on low-end devices.

**Related skills:** Build animations with **gsap-core** and **gsap-timeline**; for React cleanup use **gsap-react**.

## Prefer Transform and Opacity

Animating **transform** and **opacity** keeps work on the GPU compositor and avoids costly layout recalculations. Avoid layout-heavy properties.

**✅ FAST (use these):**
- `x`, `y`, `scale`, `rotation`, `opacity`, `autoAlpha`

**❌ SLOW (avoid these):**
- `width`, `height`, `top`, `left`, `margin`, `padding` (trigger layout recalculation and paint)

```javascript
// ✅ Good: Smooth 60fps movement using transforms
gsap.to(".card", { x: 100, y: 50, duration: 0.5 });

// ❌ Bad: Jank - triggers layout on every frame
gsap.to(".card", { left: 100, top: 50, duration: 0.5 });

// ✅ Good: Scale instead of width/height
gsap.to(".chart", { scale: 1.2, duration: 0.5 });

// ❌ Bad: Layout thrashing
gsap.to(".chart", { width: "120%", height: "120%", duration: 0.5 });
```

## will-change CSS Property

Hint to the browser that an element will animate. Apply **only to elements that are actually animating**:

```css
/* For stock card that lifts on hover */
.stock-card {
  will-change: transform;  /* Move, scale, rotate = transform */
}

/* Chart that updates in real-time */
.price-ticker {
  will-change: opacity;  /* Fade = opacity */
}
```

**⚠️ Don't overuse:** `will-change` has memory cost. Remove after animation or use judiciously.

## Batch Reads and Writes

GSAP batches updates internally, but mixing GSAP with direct DOM reads/writes can cause layout thrashing. Avoid interleaving reads and writes:

```javascript
// ✅ Good: All reads, then all writes
const heights = Array.from(document.querySelectorAll(".card"))
  .map(el => el.offsetHeight);

gsap.to(".card", {
  y: (i) => heights[i] * 2,
  stagger: 0.1
});

// ❌ Bad: Read-write-read-write alternation
document.querySelectorAll(".card").forEach((card, i) => {
  const height = card.offsetHeight;  // READ
  gsap.to(card, { y: height });      // WRITE
  const newHeight = card.offsetHeight; // READ again (layout recalc!)
});
```

## Stagger Instead of Multiple Tweens

Use **stagger** when animating many similar elements:

```javascript
// ✅ Efficient: One gsap.to() with stagger
gsap.to(".watchlist-item", {
  opacity: 1,
  x: 0,
  stagger: 0.1,  // 0.1s between each
  duration: 0.5
});

// ❌ Inefficient: Separate tween for each item
stocks.forEach((stock, i) => {
  gsap.to(`.stock-${i}`, {
    opacity: 1,
    x: 0,
    delay: i * 0.1,  // Manual delay instead of stagger
    duration: 0.5
  });
});
```

## gsap.quickTo() for Frequent Updates

For properties updated often (e.g., mouse followers, real-time price flickers), use **gsap.quickTo()** instead of creating new tweens:

```javascript
// ✅ Good: Reuse single tween for mouse follower
const updateX = gsap.quickTo("#cursor", "x", { duration: 0.5, ease: "power3" });
const updateY = gsap.quickTo("#cursor", "y", { duration: 0.5, ease: "power3" });

document.addEventListener("mousemove", (e) => {
  updateX(e.pageX);
  updateY(e.pageY);
});

// ❌ Bad: New tween created on each move
document.addEventListener("mousemove", (e) => {
  gsap.to("#cursor", { x: e.pageX, y: e.pageY, duration: 0.5 });
});
```

## ScrollTrigger Performance

- **pin: true** promotes the pinned element; pin only what's needed
- **scrub** with small value (e.g. `scrub: 1`) reduces work but use sparingly
- Call **ScrollTrigger.refresh()** only when layout changes, not on every resize

```javascript
// Good: Refresh only on actual layout changes
window.addEventListener("resize", () => {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    ScrollTrigger.refresh();  // Debounced
  }, 250);
});

// Bad: Refresh on every scroll
window.addEventListener("scroll", () => {
  ScrollTrigger.refresh();  // Performance killer
});
```

## Test on Low-End Devices

ASX traders use various devices. Test animations on:

```bash
# Mobile Chrome: Throttle CPU (4x slowdown)
# DevTools → Performance → Settings → CPU throttling
```

If 60fps can't be maintained on 4x throttle, simplify the animation.

## Memory & Cleanup

Kill off-screen animations and unused tweens:

```javascript
// Store references to tweens
const tweens = [];

document.querySelectorAll(".card").forEach(card => {
  const tween = gsap.to(card, { x: 100, duration: 0.5, paused: true });
  tweens.push(tween);
});

// Kill all tweens when navigating away
function cleanup() {
  tweens.forEach(tween => tween.kill());
  tweens.length = 0;
}

// In React: useEffect cleanup
useEffect(() => {
  // ... create animations
  
  return () => cleanup();
}, []);
```

## Real-time Data Updates (ASX Context)

For stock price tickers updating multiple times per second:

```javascript
// ✅ Efficient: Cache DOM nodes, reuse tweens
const priceDisplay = document.querySelector(".price");
const changeDisplay = document.querySelector(".change");

const updatePrice = gsap.quickTo(priceDisplay, "textContent", { duration: 0.2 });
const updateChange = gsap.quickTo(changeDisplay, "textContent", { duration: 0.2 });

// WebSocket listener
ws.addEventListener("message", (e) => {
  const { price, change } = JSON.parse(e.data);
  updatePrice(price.toFixed(2));
  updateChange(`${change >= 0 ? "+" : ""}${change.toFixed(2)}%`);
});
```

## Best Practices for ASX Dashboards

- ✅ Animate **transform** (x, y, scale, rotation) and **opacity** only
- ✅ Use **will-change** sparingly on animating elements
- ✅ Use **stagger** instead of manual delays for lists
- ✅ Use **gsap.quickTo()** for frequent updates (price tickers)
- ✅ **Clean up** tweens when components unmount
- ✅ **Debounce** ScrollTrigger.refresh() on resize
- ✅ Test on 4x CPU throttle before shipping
- ✅ Keep animations short (0.3-0.5s) for responsive feel

## Do Not

- ❌ Animate **width**, **height**, **top**, **left** — use **transform** instead
- ❌ Set **will-change** on every element "just in case" — only on animating elements
- ❌ Create hundreds of overlapping tweens without testing
- ❌ Call **ScrollTrigger.refresh()** on every resize or scroll
- ❌ Skip cleanup; stray tweens/ScrollTriggers keep running and hurt performance
- ❌ Ignore **prefers-reduced-motion** — users with vestibular issues need instant response
