---
name: web-accessibility
description: Build inclusive financial dashboards for all traders and investors. WCAG 2.1 AA compliance, keyboard navigation, screen reader support, and real-time data accessibility.
user-invokable: true
tools: ["grep_search", "read_file"]
---

# Web Accessibility Skill - ASX Prediction Platform

## Purpose
Ensure your ASX dashboards are usable by **all traders and investors**, including those with:
- Visual impairments (blindness, low vision, color blindness, astigmatism)
- Motor impairments (can't use mouse, uses keyboard/speech)
- Hearing impairments (if audio alerts are used)
- Cognitive differences (needs clear language, consistent patterns)

Financial accessibility is critical: traders with disabilities shouldn't lose access to markets.

## WCAG 2.1 AA Essentials

### 1. Perceivable — Users can SEE and UNDERSTAND your content

#### Color Contrast
```
Minimum requirements:
- Normal text: 4.5:1 ratio (WCAG AA minimum)
- Large text (18px+): 3:1 ratio
- Graphics/UI components: 3:1 ratio
- Data indicators: NEVER rely on color alone (add symbols/text)

ASX Dashboard example:
- ❌ Red price = loss (color only)
- ✅ Red price + "↓" arrow + "Loss" text
- ❌ Green bar in chart (no color-blind friendly version)
- ✅ Green bar + hatching pattern + data label
```

**Test color contrast:** Use WebAIM Color Contrast Checker:
- Light text (#F1F5F9) on dark surface (#1E293B): ~14:1 ✅ (excellent)
- Primary blue (#1E40AF) on dark surface: ~4.8:1 ✅ (barely passes)
- Error red (#EF4444) on surface: ~4.2:1 ✅ (borderline, increase weight)

#### Text Sizing & Spacing
```
- Minimum: 12px (never smaller)
- Body text default: 16px
- Mobile: Ensure text isn't cramped (line-height ≥ 1.5, 1.6 preferred)
- Allow zoom: Never set user-scalable=no
- Support text resize: Don't use fixed px heights on containers
```

#### Data Visualization Accessibility
```
Charts, tables, and real-time data:
- Every chart needs a data table alternative (or at least summary statistics)
- Color blindness: Use patterns (hatches, different line styles), not just colors
- Stock tickers: Include both visual and screen-reader friendly updates
- Price changes: Use symbols (↑/↓) + colors + percentage text
- Tooltips: Keyboard-accessible (not hover-only)

KineticLearn example → ASX adaptation:
Old: Red line = losses
New: Red line + dashed pattern + data label + legend with description
```

#### Images, Icons & Symbols
```
- Every <img> needs alt text describing the image
- Icon-only buttons need aria-label or title
- Chart icons (arrows, badges): Provide title or aria-label
- Decorative elements: alt="" (empty string, hidden from screen readers)

ASX Dashboard example:
✓ Correct:
  <img src="asx-logo.svg" alt="Australian Securities Exchange">
  <button aria-label="Add BHP to watchlist"><span aria-hidden="true">+</span></button>

✗ Wrong:
  <img src="price-up.svg"> <!-- no alt text -->
  <button>+</button> <!-- unclear what + means -->
```

#### Audio & Video (if used for alerts)
```
- Price alerts: Include visual + sound
- Sound-based notifications: ALWAYS include visual equivalent
- Background music: None (focus on data)
- Important sounds: Described in tooltips/ARIA live regions
```

### 2. Operable — Users can NAVIGATE with keyboard

#### Keyboard Navigation (Critical for Traders)
```
Essential:
- Every button/link/input/chart reachable via Tab key
- Tab moves in logical order (reading order left→right, top→bottom)
- Shift+Tab moves backward
- Enter activates buttons/links, Space for checkboxes/toggles
- Escape closes modals/menus/tooltips
- Arrow keys: Navigate table rows, chart points, or timeframes

Test Checklist:
1. Open page in Chrome
2. Press Tab repeatedly — can you reach every interactive element?
3. Can you buy/sell/add stock using only keyboard?
4. Can you view all prices and predictions?
5. Can you close any popup or modal with Escape?

ASX Dashboard keyboard support:
  - Tab: Navigate between cards, tables, buttons
  - Arrow Up/Down: Change selected stock in table
  - Arrow Left/Right: Switch between timeframes (1M, 3M, 1Y)
  - Enter: Open stock details or execute trade
  - Escape: Close modals, deselect rows
  - Ctrl+F: Search ticker (browser native)
```

#### Focus Indicators
```
NEVER remove focus outlines. Redesign them instead:

❌ Bad:
*:focus { outline: none; }

✅ Good (ASX Dashboard):
button:focus {
  outline: 2px solid #1E40AF; /* primary blue */
  outline-offset: 2px;
}
input:focus {
  border: 2px solid #1E40AF;
  box-shadow: 0 0 0 3px rgba(30, 64, 175, 0.1);
}

- Visible on all interactive elements
- Contrasts with background (≥3:1)
- At least 2px thick
- Brand color (#1E40AF primary blue)
```

#### Skip Navigation
```
Add first on every page:

<a href="#main-content" class="sr-only" tabindex="1">
  Skip to main dashboard
</a>

Visible only when focused, hidden otherwise.
Allows keyboard users to skip repeated navigation (top bar, menu).
```

#### No Time Limits on Data Display
```
- Don't auto-dismiss price alerts
- If timeout needed, allow pause/extend
- Sessions shouldn't expire during active trading
- Real-time data updates: Don't auto-rotate or auto-scroll
- Users control pagination, not automatic refresh

Exception: Session timeout for security (60+ mins) is acceptable if warned
```

### 3. Understandable — Users can READ and COMPREHEND

#### Clear Language for Financial Data
```
Use:
- Simple sentence structure (10th grade reading level)
- Plain English (avoid jargon OR define it)
- Short labels (3-5 words max on buttons)
- Consistent terminology (don't switch between "profit", "gain", "return")

For prices & metrics:
- Always include units: "$45.32 AUD", "+2.1%", "2.4M shares"
- Explain abbreviations first use: "ASX (Australian Securities Exchange)"
- Use consistent number formatting: $1,234.56 or 1234.56 (pick one format)

❌ Bad: "Adjust risk-weighted capital exposure metrics"
✅ Good: "Change position size"

❌ Bad: "45.32" (what is this?)
✅ Good: "$45.32 AUD"
```

#### Consistent Navigation & Patterns
```
- Dashboard menu in same location (left sidebar or top nav)
- Buttons do same thing every time (Save = Save, Cancel = Cancel)
- Stock cards in consistent order (Symbol, Price, Change, Action button)
- Charts always show same timeframes in same order
- Color meanings: Green = gains, Red = losses (always)

Traders rely on muscle memory; inconsistency = lost productivity
```

#### Error Messages & Alerts
```
Every error/alert must include:
1. WHAT went wrong (specific, not generic)
2. WHY it happened
3. HOW to fix it
4. Keep tone professional, not condescending

❌ Bad: "Error"
✅ Good: "Trade failed: Insufficient buying power (need $5,000, have $3,200). 
         Reduce quantity or deposit funds."

For real-time alerts:
- Show in ARIA live region (screen readers announce immediately)
- Display on screen for 5+ seconds (not auto-dismiss)
- Allow manual dismiss (close button)
- Color + icon + text (never relying on one)
```

#### Forms (Watchlist, Orders)
```
All inputs need:
- <label> clearly associated with <input> (not placeholder alone)
- Hint text above input (not in placeholder)
- Required fields marked * or with text "required"
- Clear error messages (not just red borders)
- Helpful hints for complex fields

ASX order form example:
<div>
  <label for="quantity">Quantity (required)</label>
  <input id="quantity" type="number" min="1" required />
  <p class="hint">Number of shares (minimum 1)</p>
</div>
```

#### Tables (Stock Data, Tracking)
```
- Headers: <th scope="col"> or <th scope="row"> for clarity
- Row headers: "BHP.ASX" as <th> so screen reader knows context
- Data cells: Use <td> with proper alignment (right for numbers)
- Captions: <caption> at top explaining table purpose
- Summaries: Total row at bottom clearly marked

Example:
<table>
  <caption>14-Day ASX Tracking - Top 10 Performers</caption>
  <thead>
    <tr>
      <th scope="col">Symbol</th>
      <th scope="col">Entry Price</th>
      <th scope="col">Current Price</th>
      <th scope="col">Return</th>
      <th scope="col">Status</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th scope="row">BHP.ASX</th>
      <td>$44.00</td>
      <td>$45.32</td>
      <td>+3.0%</td>
      <td>Meeting target ✓</td>
    </tr>
  </tbody>
</table>
```

### 4. Robust — Works with ASSISTIVE TECHNOLOGY

#### ARIA (Accessible Rich Internet Applications)
```
Use only when native HTML won't work:

aria-label: For icon buttons
<button aria-label="Delete watchlist">✕</button>
<button aria-label="Add to watchlist">+</button>

aria-describedby: Extra description for complex widgets
<input type="text" aria-describedby="price-hint" />
<p id="price-hint">Enter price in AUD (e.g., 45.32)</p>

aria-live: Real-time data updates (MUST use for price tickers)
<div aria-live="polite" aria-atomic="true">
  BHP.ASX: $45.32 ↑ +2.1%
</div>
  - "polite": Announce when quiet, don't interrupt
  - "atomic": Read entire region on update

aria-hidden: Hide decorative elements
<span aria-hidden="true">→</span> <!-- arrow only for sighted users -->

aria-current: Mark current page/tab in navigation
<a href="/dashboard" aria-current="page">Dashboard</a>

role: Explicit roles for custom components
<div role="heading" aria-level="2">Stock Price Chart</div>
```

#### Screen Reader Testing
```
Test with NVDA (free, Windows) or JAWS (macOS):
1. Disable mouse, navigate entire dashboard with keyboard
2. Test data tables — can you understand structure?
3. Test real-time updates — are prices announced?
4. Test charts — can you access data table alternative?
5. Test forms — can you fill in and submit?
6. Test alerts — do you hear error messages?

ASX specific:
- Stock cards: "BHP.ASX, $45.32, up 2.1%, meets target"
- Charts: "12-week prediction, starting $44.00, ending $48.50, confidence 87%"
- Alerts: "Price alert: CBA fell below support at $145.20"
```

#### Reduced Motion (Important!)
```
Many users with vestibular/neurological conditions can't use sites with animations.

Respect prefers-reduced-motion:
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}

With GSAP:
useGSAP(() => {
  const prefersReduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  
  if (!prefersReduced) {
    // Run animations normally
    gsap.to(".price", { x: 100, duration: 0.3 });
  } else {
    // Instant state change, no animation
    gsap.set(".price", { x: 100 });
  }
});
```

#### Semantic HTML
```
Use proper semantic elements (not all <div>s):

✅ Good:
<header>
  <nav>Dashboard Menu</nav>
</header>
<main id="main-content">
  <section>Stock Watchlist</section>
  <article>BHP Analysis</article>
</main>
<aside>Related Links</aside>
<footer>Disclaimer</footer>

❌ Bad:
<div class="header">
  <div class="nav">Dashboard Menu</div>
</div>
<div class="main">
  <div class="section">Stock Watchlist</div>
</div>
```

## Accessibility Checklist for ASX Dashboards

- [ ] **Color contrast**: All text ≥4.5:1, all interactive ≥3:1 (WCAG AA)
- [ ] **Focus indicators**: Visible 2px outline on all focusable elements
- [ ] **Touch targets**: ≥44×44px on mobile
- [ ] **Keyboard navigation**: Entire dashboard usable with keyboard only
- [ ] **Skip links**: Skip to main content available on first Tab
- [ ] **Data clarity**: Color + symbol redundancy (never color-only)
- [ ] **Screen readers**: ARIA labels on icons, semantic HTML, live regions for updates
- [ ] **Real-time data**: Updates announced via aria-live (not just visual)
- [ ] **Forms**: All inputs have associated labels, clear errors
- [ ] **Tables**: Headers with scope, data structure clear
- [ ] **Reduced motion**: Animations disabled for users with prefers-reduced-motion
- [ ] **Number formatting**: Always include units ($, %, K, M)
- [ ] **Charts**: Data table alternative provided
- [ ] **Testing**: Manual keyboard test + NVDA/JAWS screen reader test
