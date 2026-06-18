---
name: gsap-react
description: Official GSAP skill for React — useGSAP hook, refs, gsap.context(), cleanup. Use for smooth ASX dashboard animations in React/Vite.
license: MIT
---

# GSAP with React - ASX Dashboard Implementation

## When to Use This Skill

Apply when writing GSAP code in React (frontend, broker-frontend): stock card animations, chart updates, real-time price tweens, or avoiding cleanup/SSR issues.

**Related skills:** For tweens/timelines use **gsap-core** and **gsap-timeline**; for scroll animation use **gsap-scrolltrigger**; for performance use **gsap-performance**.

## Installation

```bash
# Install GSAP
npm install gsap

# Install React integration
npm install @gsap/react
```

## Prefer useGSAP() Hook

Use **useGSAP()** from `@gsap/react` instead of `useEffect()`. It handles cleanup automatically and provides scoped selectors.

```javascript
import { useGSAP } from "@gsap/react";
import gsap from "gsap";
import { useRef } from "react";

gsap.registerPlugin(useGSAP);

export function StockCard({ symbol, price }) {
  const containerRef = useRef(null);
  
  useGSAP(() => {
    // All GSAP code here is scoped to containerRef
    gsap.from(".card", { opacity: 0, y: 20, duration: 0.5 });
    gsap.to(".price", { textContent: price, duration: 0.3 });
  }, { scope: containerRef });
  
  return <div ref={containerRef} className="card">
    <span className="price">{price}</span>
  </div>;
}
```

**Key benefits:**
- ✅ Cleanup runs automatically on unmount
- ✅ Selectors scoped to `containerRef` (no cross-component interference)
- ✅ ScrollTriggers cleaned up automatically
- ✅ Easier than `gsap.context()` + manual cleanup

## Refs for Targets

Use **refs** so GSAP targets the actual DOM nodes:

```javascript
export function PriceDisplay() {
  const priceRef = useRef(null);
  const changeRef = useRef(null);
  
  useGSAP(() => {
    // Target specific elements via refs
    gsap.to(priceRef.current, { textContent: 45.32, duration: 0.3 });
    gsap.to(changeRef.current, {
      color: "#10B981",  // green for gain
      duration: 0.3
    });
  }, { scope: containerRef });
  
  return <>
    <span ref={priceRef}>$45.00</span>
    <span ref={changeRef}>+0.32</span>
  </>;
}
```

## Dependency Array & revertOnUpdate

By default, useGSAP runs once on mount (empty dependency array). Control re-execution:

```javascript
// Run once on mount
useGSAP(() => {
  gsap.to(".box", { x: 100 });
}, { scope: containerRef });

// Re-run when `price` changes
useGSAP(() => {
  gsap.to(".price-display", { textContent: price, duration: 0.5 });
}, { scope: containerRef, dependencies: [price] });

// Revert and re-run on dependency change
useGSAP(() => {
  gsap.to(".card", { backgroundColor: selectedColor });
}, { 
  scope: containerRef, 
  revertOnUpdate: true,
  dependencies: [selectedColor]
});
```

## gsap.context() in useEffect (When useGSAP isn't available)

If you can't use `@gsap/react`, use `gsap.context()` in `useEffect`:

```javascript
import { useEffect, useRef } from "react";
import gsap from "gsap";

export function Chart() {
  const containerRef = useRef(null);
  
  useEffect(() => {
    // Create GSAP context
    const ctx = gsap.context(() => {
      gsap.from(".chart-line", { strokeDashoffset: 1000, duration: 1.5 });
    }, containerRef);
    
    // Cleanup
    return () => ctx.revert();
  }, []);
  
  return <svg ref={containerRef}>
    <polyline className="chart-line" points="..." />
  </svg>;
}
```

**Critical:** Always return cleanup that calls `ctx.revert()`.

## Context-Safe Callbacks

Use **contextSafe** for callbacks executed AFTER useGSAP runs (e.g., event handlers):

```javascript
export function WatchlistCard() {
  const cardRef = useRef(null);
  const [isHovered, setIsHovered] = useState(false);
  
  useGSAP((context, contextSafe) => {
    // ✅ Animation created during useGSAP execution
    gsap.from(cardRef.current, { opacity: 0, duration: 0.3 });
    
    // ❌ DANGER: Event handler creates animation AFTER useGSAP
    // This won't be cleaned up
    cardRef.current.addEventListener("mouseenter", () => {
      gsap.to(cardRef.current, { y: -8 });
    });
    
    // ✅ SAFE: Wrap event handler callback with contextSafe
    const handleMouseEnter = contextSafe(() => {
      gsap.to(cardRef.current, { y: -8, duration: 0.3 });
    });
    
    const handleMouseLeave = contextSafe(() => {
      gsap.to(cardRef.current, { y: 0, duration: 0.3 });
    });
    
    cardRef.current.addEventListener("mouseenter", handleMouseEnter);
    cardRef.current.addEventListener("mouseleave", handleMouseLeave);
    
    // Cleanup event listeners
    return () => {
      cardRef.current?.removeEventListener("mouseenter", handleMouseEnter);
      cardRef.current?.removeEventListener("mouseleave", handleMouseLeave);
    };
  }, { scope: cardRef });
  
  return <div ref={cardRef} className="card">...</div>;
}
```

## Real-time Data Updates (Price Tickers)

Animate price changes smoothly:

```javascript
export function StockTicker({ price, previousPrice }) {
  const priceRef = useRef(null);
  
  // Re-run animation when price changes
  useGSAP(() => {
    const change = price - previousPrice;
    const color = change >= 0 ? "#10B981" : "#EF4444";
    
    // Animate price text and flash color
    const tl = gsap.timeline();
    tl.to(priceRef.current, { textContent: price.toFixed(2), duration: 0.3 })
      .to(priceRef.current, { color, duration: 0.2 }, 0);
      
  }, { scope: priceRef, dependencies: [price] });
  
  return <span ref={priceRef}>${price.toFixed(2)}</span>;
}
```

## Reduced Motion Accessibility

Respect user preferences:

```javascript
export function PredictionBadge({ confidence }) {
  const badgeRef = useRef(null);
  
  useGSAP(() => {
    const prefersReduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)"
    ).matches;
    
    if (!prefersReduced) {
      // Animate normally
      gsap.from(badgeRef.current, {
        opacity: 0,
        scale: 0.8,
        duration: 0.4,
        ease: "back.out(1.2)"
      });
    } else {
      // Instant render, no animation
      gsap.set(badgeRef.current, { opacity: 1, scale: 1 });
    }
  }, { scope: badgeRef });
  
  return <div ref={badgeRef} className="badge">{confidence}% confidence</div>;
}
```

## List/Staggered Animations

Animate multiple cards with stagger:

```javascript
export function WatchlistCards({ stocks }) {
  const containerRef = useRef(null);
  
  useGSAP(() => {
    // Stagger entrance for each card
    gsap.from(".stock-card", {
      opacity: 0,
      y: 20,
      stagger: 0.1,
      duration: 0.5,
      ease: "power2.out"
    });
  }, { scope: containerRef });
  
  return <div ref={containerRef} className="watchlist">
    {stocks.map(stock => (
      <div key={stock.id} className="stock-card">
        {stock.symbol}
      </div>
    ))}
  </div>;
}
```

## SSR & Next.js Considerations

GSAP runs in browser only. Ensure client-only execution:

```javascript
// Option 1: useGSAP automatically handles client-only
useGSAP(() => {
  // This only runs on client
  gsap.to(".box", { x: 100 });
}, { scope: ref });

// Option 2: Manual check if needed
useEffect(() => {
  if (typeof window === "undefined") return;
  
  gsap.to(".box", { x: 100 });
}, []);
```

## Common Patterns for ASX Dashboards

### 1. Stock Card Entrance
```javascript
useGSAP(() => {
  const tl = gsap.timeline();
  tl.from(".card", { opacity: 0, y: 20, duration: 0.4 })
    .from(".price", { opacity: 0, x: -10, duration: 0.3 }, "<0.1")
    .from(".change-badge", { opacity: 0, scale: 0.8, duration: 0.3 }, "<0.1");
}, { scope: containerRef });
```

### 2. Real-time Price Update
```javascript
useGSAP(() => {
  gsap.to(priceRef.current, {
    textContent: newPrice,
    color: newPrice > oldPrice ? "#10B981" : "#EF4444",
    duration: 0.3
  });
}, { scope: priceRef, dependencies: [newPrice] });
```

### 3. Chart Curve Animation
```javascript
useGSAP(() => {
  gsap.to(".chart-path", {
    strokeDashoffset: 0,
    duration: 1.2,
    ease: "power2.inOut"
  });
}, { scope: chartRef, dependencies: [chartData] });
```

## Best Practices

- ✅ Prefer **useGSAP** over `useEffect` for cleaner cleanup
- ✅ Always pass **scope** (containerRef) to scope selectors
- ✅ Use **refs** for specific targets
- ✅ Wrap event handler animations with **contextSafe**
- ✅ Use **dependencies** to re-run on data changes
- ✅ Respect **prefers-reduced-motion** for accessibility
- ✅ Keep animations short (0.3-0.5s) for responsive feel

## Do Not

- ❌ Run GSAP during SSR (use useGSAP or useEffect, not top-level code)
- ❌ Target selectors without **scope** (can match unintended elements)
- ❌ Skip cleanup (always return `() => ctx.revert()` if using `gsap.context()`)
- ❌ Create new tweens on every render (memoize or store in timeline)
- ❌ Forget **contextSafe** for event handler animations
