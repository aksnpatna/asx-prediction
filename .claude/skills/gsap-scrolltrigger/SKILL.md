---
name: gsap-scrolltrigger
description: Official GSAP skill for ScrollTrigger — scroll-linked animations, pinning, scrub, triggers. Use for ASX dashboard scroll-based chart animations and lazy loading.
license: MIT
---

# GSAP ScrollTrigger - ASX Dashboard Scroll Animations

## When to Use This Skill

Apply when implementing scroll-driven animations in ASX dashboards: triggering chart reveals on scroll, pinning section headers, or when the user mentions scroll animation, parallax, or pinning.

**Related skills:** For tweens/timelines use **gsap-core** and **gsap-timeline**; for React cleanup use **gsap-react**; for performance use **gsap-performance**.

## Registering the Plugin

ScrollTrigger is a plugin. Register it once:

```javascript
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);
```

## Basic Trigger

Tie an animation to scroll position:

```javascript
gsap.to(".chart-container", {
  scrollTrigger: {
    trigger: ".chart-container",
    start: "top 80%",          // when top of trigger hits 80% from top (enter viewport)
    end: "bottom 20%",         // when bottom hits 20% from top (leave viewport)
    toggleActions: "play reverse play reverse"
    // onEnter: play, onLeave: reverse, onEnterBack: play, onLeaveBack: reverse
  },
  opacity: 1,
  y: 0,
  duration: 0.8
});
```

## Key Configuration Options

| Property | Type | Description |
|----------|------|-------------|
| **trigger** | String \| Element | Element whose position activates the trigger. Required. |
| **start** | String \| Number | When trigger becomes active. Default `"top bottom"`. Format: `"triggerPosition viewportPosition"` |
| **end** | String \| Number | When trigger ends. Default `"bottom top"`. |
| **endTrigger** | String \| Element | Element used for **end** (different from trigger). |
| **toggleActions** | String | Four actions: `"onEnter onLeave onEnterBack onLeaveBack"`. Each: `"play"`, `"pause"`, `"resume"`, `"reverse"`, `"none"`. |
| **scrub** | Boolean \| Number | Link animation to scroll. `true` = direct; number = seconds to "catch up". |
| **pin** | Boolean \| String | Pin element during active. `true` = pin trigger. |
| **pinSpacing** | Boolean | `true` (default) = add spacer; `false` = no spacer. |
| **markers** | Boolean \| Object | Dev markers for debugging. Remove in production. |
| **once** | Boolean | Kill after first trigger. |
| **refreshPriority** | Number | Lower = refreshed first (use for non-top-to-bottom triggers). |

## Scroll Position Format

**start** / **end** format: `"triggerPosition viewportPosition"`

```javascript
"top top"      // trigger top hits viewport top
"top 80%"      // trigger top hits 80% down viewport (enter view)
"center center"
"bottom 20%"   // trigger bottom hits 20% down viewport (exit view)
"top center+=100px"  // plus 100px offset
```

Alternatively, numeric pixel values:

```javascript
start: 500     // when scrolled 500px from top
end: "+=300"   // 300px past start
```

## Basic Trigger Example: Reveal on Scroll

```javascript
// Reveal chart when scrolling into view
gsap.from(".prediction-chart", {
  scrollTrigger: {
    trigger: ".prediction-chart",
    start: "top 80%",
    toggleActions: "play reverse play reverse"
  },
  opacity: 0,
  y: 50,
  duration: 0.8,
  ease: "power2.out"
});
```

## Scrub: Tie Animation to Scroll Position

Link animation progress directly to scroll:

```javascript
// Chart fills as you scroll (scrubbed to scroll position)
gsap.from(".chart-fill", {
  scrollTrigger: {
    trigger: ".chart-fill",
    start: "top center",
    end: "bottom center",
    scrub: 1  // 1 second for playhead to catch up (smoother)
  },
  scaleY: 0,
  transformOrigin: "bottom",
  duration: 1
});
```

- `scrub: true` — direct link (may feel jerky)
- `scrub: 1` — 1 second ease-in for smoother feel (recommended)

## Pin: Stick Element During Scroll

Pin header while content scrolls below:

```javascript
gsap.to(".dashboard-header", {
  scrollTrigger: {
    trigger: ".dashboard-header",
    pin: true,           // pin the trigger element
    start: "top top",    // when top hits viewport top
    end: "+=500",        // pin for 500px of scroll
    markers: { startColor: "green", endColor: "red" }  // dev markers
  }
});
```

**Don't animate the pinned element itself; animate children:**

```javascript
// ✅ Good: Pin header, animate content inside
gsap.to(".dashboard-header .title", {
  scrollTrigger: {
    trigger: ".dashboard-header",
    start: "top top",
    pin: true
  },
  opacity: 0.5
});

// ❌ Bad: Animate pinned element itself
gsap.to(".dashboard-header", {
  scrollTrigger: {
    trigger: ".dashboard-header",
    pin: true
  },
  x: 100  // conflicts with pinning
});
```

## Standalone ScrollTrigger (No Animation)

Use ScrollTrigger.create() for custom behavior:

```javascript
// Trigger custom code on scroll
ScrollTrigger.create({
  trigger: ".watchlist",
  start: "top 50%",
  onEnter: () => {
    console.log("User scrolled to watchlist");
    loadMoreStocks();  // lazy load
  },
  onLeave: () => {
    console.log("User left watchlist");
    pauseRealTimeUpdates();
  }
});
```

## ScrollTrigger.batch() for Animations

**Batch** groups similar elements and coordinates their animations:

```javascript
// Animate all visible items at once (great for lists)
ScrollTrigger.batch(".stock-card", {
  onEnter: (elements) => {
    gsap.to(elements, {
      opacity: 1,
      y: 0,
      stagger: 0.1,
      duration: 0.5,
      overwrite: true
    });
  },
  onLeave: (elements) => {
    gsap.set(elements, { opacity: 0, y: 50, overwrite: true });
  },
  start: "top 80%",
  end: "bottom 20%",
  interval: 0.1,      // batch within 0.1s intervals
  batchMax: 3         // max 3 elements per batch
});
```

**Benefits:**
- Smoother stagger (all visible items animate together)
- More efficient than ScrollTrigger per-element
- Better for long lists

## ASX Dashboard Examples

### 1. Lazy Load Charts on Scroll

```javascript
ScrollTrigger.create({
  trigger: ".chart-section",
  start: "top 80%",
  once: true,  // trigger only once
  onEnter: () => {
    loadChartData();  // fetch data when entering view
    
    gsap.from(".chart-canvas", {
      opacity: 0,
      duration: 0.6,
      ease: "power2.out"
    });
  }
});
```

### 2. Pin Header While Scrolling Table

```javascript
gsap.to(".data-table-header", {
  scrollTrigger: {
    trigger: ".data-table",
    start: "top top",
    pin: ".data-table-header",
    end: "bottom top",
    pinSpacing: true  // add spacer so layout doesn't collapse
  }
});
```

### 3. Animate Table Rows on Scroll

```javascript
ScrollTrigger.batch(".table-row", {
  onEnter: (rows) => {
    gsap.to(rows, {
      opacity: 1,
      x: 0,
      stagger: 0.05,
      duration: 0.4,
      ease: "power2.out"
    });
  },
  start: "top 85%"
});
```

### 4. Scrub Chart to Scroll Position

```javascript
gsap.to(".profit-bar", {
  scrollTrigger: {
    trigger: ".profit-section",
    start: "top center",
    end: "bottom center",
    scrub: 1.5  // smooth catch-up
  },
  width: "100%",
  duration: 1
});
```

## React with ScrollTrigger

Use with `useGSAP` from `@gsap/react`:

```javascript
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

export function Dashboard() {
  const containerRef = useRef(null);
  
  useGSAP(() => {
    // All ScrollTriggers are scoped to containerRef
    // Cleanup is automatic on unmount
    
    gsap.from(".chart", {
      scrollTrigger: {
        trigger: ".chart",
        start: "top 80%"
      },
      opacity: 0,
      y: 50,
      duration: 0.8
    });
  }, { scope: containerRef });
  
  return <div ref={containerRef}>
    {/* Dashboard content */}
  </div>;
}
```

## Refresh on Dynamic Content

When content changes (e.g., window resize, new data), refresh ScrollTriggers:

```javascript
// Debounced refresh on resize
let refreshTimer;
window.addEventListener("resize", () => {
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    ScrollTrigger.refresh();
  }, 250);
});

// Refresh after loading new stocks
async function loadMoreStocks() {
  await fetch("/api/stocks");
  // ...
  ScrollTrigger.refresh();  // recalculate trigger positions
}
```

## Best Practices

- ✅ Use **ScrollTrigger.batch()** for lists (more efficient than per-element)
- ✅ Use **scrub: 1+** for smooth scrolled animations (not `true`)
- ✅ **Debounce** ScrollTrigger.refresh() on resize
- ✅ Use **once: true** for one-time animations (reveal on scroll)
- ✅ Use dev **markers** during development, remove for production
- ✅ **Pin** only necessary elements (header, sidebars)
- ✅ Test on mobile (pinning can be quirky on touch)

## Do Not

- ❌ Animate a pinned element itself — animate children only
- ❌ Create ScrollTriggers with `scrub: true` (use number like `scrub: 1`)
- ❌ Call **ScrollTrigger.refresh()** on every scroll event (use debounce)
- ❌ Nest ScrollTriggers inside other ScrollTriggers
- ❌ Leave dev **markers** in production (remove for clean release)
- ❌ Create ScrollTriggers that aren't cleaned up (use React's useGSAP for automatic cleanup)
