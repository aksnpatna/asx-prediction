---
name: gsap-timeline
description: Official GSAP skill for timelines — gsap.timeline(), position parameter, nesting, playback. Use for sequencing ASX dashboard animations.
license: MIT
---

# GSAP Timeline - ASX Dashboard Sequences

## When to Use This Skill

Apply when building multi-step animations for ASX dashboards: stock card entrance sequences, chart reveal sequences, or when coordinating several tweens in specific order.

**Related skills:** For single tweens use **gsap-core**; for scroll-driven timelines use **gsap-scrolltrigger**; for React use **gsap-react**.

## Creating a Timeline

```javascript
const tl = gsap.timeline();
tl.to(".card-header", { opacity: 1, y: 0, duration: 0.3 })
  .to(".card-content", { opacity: 1, y: 0, duration: 0.3 })
  .to(".card-footer", { opacity: 1, y: 0, duration: 0.3 });
```

By default, tweens are **appended** one after another. Use the **position parameter** to place tweens at specific times or overlapping.

## Position Parameter (Control Timing)

Third argument controls placement:

- **Absolute**: `1` — start at 1 second into timeline
- **Relative (default)**: `"+=0.2"` — 0.2s after previous tween ends; `"-=0.1"` — 0.1s before previous ends
- **Label**: `"cardShown"` — at that label; `"cardShown+=0.3"` — 0.3s after label
- **Placement**: `"<"` — start when previous started; `">"` — start when previous ended (default); `"<0.2"` — 0.2s after previous started

Examples for ASX stock card:

```javascript
const tl = gsap.timeline();
tl.to(".card", { opacity: 1 }, 0);           // at start (0s)
tl.to(".price", { textContent: "$45.32" }, "+=0.1");  // 0.1s after card fades in
tl.to(".change", { color: "#10B981" }, "<");  // same time as price update
tl.to(".badge", { scale: 1 }, "<0.1");       // 0.1s after price/change start
```

## Timeline Defaults

Pass defaults so all child tweens inherit settings:

```javascript
const tl = gsap.timeline({
  defaults: {
    duration: 0.3,
    ease: "power2.out"
  }
});

tl.to(".card-1", { opacity: 1, y: 0 })  // uses 0.3s and power2.out
  .to(".card-2", { opacity: 1, y: 0 })  // uses 0.3s and power2.out
  .to(".card-3", { opacity: 1, y: 0 }); // uses 0.3s and power2.out
```

## Timeline Options

- **paused: true** — create paused; call `.play()` to start.
- **repeat**, **yoyo** — apply to whole timeline.
- **onComplete**, **onStart**, **onUpdate** — timeline-level callbacks.
- **defaults** — vars merged into every child tween.

```javascript
const tl = gsap.timeline({
  paused: true,
  defaults: { duration: 0.4, ease: "power2.out" }
});

// Later...
tl.play();  // start animation
```

## Labels (For readable code)

Add labels for maintainability:

```javascript
const tl = gsap.timeline();

tl.addLabel("cardEnter", 0);
tl.to(".card", { opacity: 1, y: 0 }, "cardEnter");

tl.addLabel("dataLoad", "+=0.3");
tl.to(".price", { textContent: "$45.32" }, "dataLoad");

tl.addLabel("complete", "+=0.2");
tl.to(".badge", { scale: 1 }, "complete");

// Play from specific label
tl.play("dataLoad");  // skip card enter, start at data load
```

## Nesting Timelines

Timelines can contain other timelines:

```javascript
const cardTl = gsap.timeline();
cardTl.to(".card-header", { opacity: 1 })
      .to(".card-body", { opacity: 1 });

const masterTl = gsap.timeline();
masterTl.add(cardTl, 0);        // add at start
masterTl.to(".overlay", { autoAlpha: 0 }, "+=0.5");
```

## Controlling Playback

- **tl.play()** / **tl.pause()**
- **tl.reverse()** — play timeline backward
- **tl.restart()** — from beginning
- **tl.time(1.5)** — seek to 1.5 seconds
- **tl.progress(0.5)** — seek to 50%
- **tl.kill()** — kill timeline and its children

```javascript
const tl = gsap.timeline({ paused: true });
tl.to(".card", { x: 100, duration: 1 })
  .to(".card", { y: 50, duration: 0.5 });

// User clicks button to trigger animation
document.querySelector("#animate-btn").addEventListener("click", () => {
  tl.restart();  // always start from beginning
});

// User can pause/resume
document.querySelector("#pause-btn").addEventListener("click", () => {
  tl.paused(!tl.paused());
});
```

## ASX Dashboard Example: Stock Card Entrance

```javascript
// 14-day tracking card entrance sequence
function createCardTimeline(cardElement) {
  const tl = gsap.timeline();
  
  tl.addLabel("start", 0);
  
  // Card background fades in and lifts
  tl.from(cardElement, {
    opacity: 0,
    y: 20,
    duration: 0.4,
    ease: "power2.out"
  }, "start");
  
  // Price text appears slightly delayed
  tl.from(cardElement.querySelector(".price"), {
    opacity: 0,
    x: -10,
    duration: 0.3,
    ease: "power2.out"
  }, "start+=0.15");
  
  // Change indicator (colored badge) appears
  tl.from(cardElement.querySelector(".change-badge"), {
    opacity: 0,
    scale: 0.8,
    duration: 0.3,
    ease: "back.out(1.2)"
  }, "start+=0.25");
  
  // Prediction badge slides in from right
  tl.from(cardElement.querySelector(".prediction-badge"), {
    opacity: 0,
    x: 20,
    duration: 0.3,
    ease: "power2.out"
  }, "start+=0.35");
  
  return tl;
}

// Apply to all cards with stagger
const masterTl = gsap.timeline();
document.querySelectorAll(".stock-card").forEach((card, i) => {
  const cardTl = createCardTimeline(card);
  masterTl.add(cardTl, i * 0.1);  // stagger by 0.1s
});
```

## Best Practices

- ✅ Prefer **timelines** for multi-step animations
- ✅ Use the **position parameter** for precise timing control
- ✅ Add **labels** for readable, maintainable sequencing
- ✅ Pass **defaults** so child tweens inherit duration and ease
- ✅ Store timeline reference when controlling playback
- ✅ Nest timelines for complex, reusable sequences
- ✅ Keep total duration < 1.5s for dashboard animations (data moves fast)

## Do Not

- ❌ Chain animations with **delay** when **timeline** can sequence them
- ❌ Create new timelines every render (store in ref/state)
- ❌ Forget **defaults** when many tweens share same duration/ease
- ❌ Use complex nested timelines for simple animations (use single tween instead)
- ❌ Nest ScrollTriggers; put ScrollTrigger on top-level tween/timeline only
