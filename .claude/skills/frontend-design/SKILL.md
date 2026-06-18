---
name: frontend-design
description: Build professional financial dashboards with clean aesthetics, clear data hierarchy, and smooth micro-interactions. ASX prediction platform UI/UX best practices.
user-invokable: true
tools: ["read_file", "replace_string_in_file", "create_file"]
---

# Frontend Design Skill - ASX Prediction Platform

## Purpose
Create professional, data-driven financial dashboards that prioritize clarity, trust, and real-time usability. Every component should facilitate quick decision-making with elegant, purposeful interactions.

## Design Principles

### 1. Aesthetic Direction: Professional Financial Dashboard
Choose a clean, modern approach that conveys:
- **Trust & Authority** — Solid foundations, predictable interactions
- **Data Clarity** — Information hierarchy guides the eye to key metrics
- **Professional Polish** — Refined spacing, measured animations, minimal cognitive load
- **Real-time Responsiveness** — Smooth updates without distraction

### 2. Typography Strategy
**Typography for Financial Data**

- **Headlines**: IBM Plex Sans Bold (700) or Roboto Bold — professional, readable at all sizes
- **Body/Numbers**: IBM Plex Mono (regular) or JetBrains Mono for price data, volumes, percentages
- **Labels**: IBM Plex Sans (500) — consistent labeling for axes, legends, metrics
- **Accent text**: IBM Plex Sans SemiBold (600) + UPPERCASE for CTAs (e.g., "ADD TO WATCHLIST", "CONFIRM TRADE")

Font pairing rule: Bold sans-serif for structure + Monospace for numerical data = professional clarity.

### 3. Color Palette
**1 Primary + 1 Accent + Semantic + Neutrals = Financial Harmony**

ASX Dashboard colors:
```
Primary:     #1E40AF (Professional Blue) — trust, data, stability
Accent:      #10B981 (Emerald) — gains, bullish, positive signals
Semantic:
  - Gains:   #10B981 (Green) — price up, positive change
  - Losses:  #EF4444 (Red) — price down, losses
  - Neutral: #6B7280 (Gray) — unchanged, pending
  - Warning: #F59E0B (Amber) — volatility, caution signals
  
Neutrals:
  - BG:      #0F172A (Deep slate) — modern dark dashboard
  - Surface: #1E293B (Card background)
  - FG:      #F1F5F9 (Light text on dark)
  - Border:  #334155 (Subtle dividers)
  - FG-Alt:  #94A3B8 (Secondary text, muted)
```

### 4. Micro-Interactions

Every interactive element responds purposefully:

#### Button States (CTAs)
```
Idle:     Primary color, subtle depth
Hover:    +15% brightness, lift shadow, slight scale(1.02)
Active:   scale(0.98), shadow contracts (press feedback)
Disabled: 50% opacity, grayscale, pointer-events-none
Focus:    2px outline matching primary color, inset offset 2px
```

#### Stock Card / Data Row
- **Idle**: Border hint, flat shadow
- **Hover**: Border brightens, shadow grows, background slightly elevated
- **Tap**: Row highlights with accent color background (10% opacity)

#### Price Change Indicator
- **Up**: Green text + upward arrow, animate ↑ scale entrance
- **Down**: Red text + downward arrow, animate ↓ scale entrance
- **Flat**: Gray text, no animation

#### Loading & Real-time Updates
- **Data arriving**: Skeleton pulse on empty rows (smooth opacity pulse 1.5s)
- **Price update**: Brief accent-color flash (opacity 0.2, 300ms) on changed cell
- **Chart updating**: Smooth curve animation when data point added (GSAP cubic-bezier)

### 5. Responsive Patterns

#### Mobile-First (375px+)
```
- Touch targets: min 44×44px (critical for trading)
- Padding: 12px mobile → 16px tablet → 24px desktop
- Font sizes: 14px body (mobile) → 16px (tablet) → 18px (desktop)
- Columns: 1 mobile, stacked cards; 2 tablet; 3+ desktop
- Charts: 100% width, scaled by container
```

#### Tablet (640px+) & Desktop (1024px+)
- Side-by-side layouts for charts + data panels
- Multi-column tables with horizontal scroll fallback
- Fixed header for persistent navigation & key metrics
- Hover states on pointer devices only

### 6. Data Visualization Accessibility

- **Color is not the only indicator**: Always pair color with symbols (↑/↓, +/-, checkmark/X)
- **Number formatting**: Always show units (%, $, K, M) next to values
- **Contrast**: Ensure chart lines/bars have ≥3:1 contrast with background
- **Hover tooltips**: Provide detailed values on hover (keyboard: focus reveals)

## Implementation Checklist

When designing ASX dashboard screens, ensure:

- [ ] Aesthetic is consistent: professional, dark modern dashboard
- [ ] Typography uses approved fonts: IBM Plex Sans, IBM Plex Mono (NOT Inter)
- [ ] Color palette uses financial colors: Green for gains, Red for losses, Blue for primary actions
- [ ] Every button/input has: idle, hover, active, disabled, focus states
- [ ] Data cards have elevation/hover feedback (raised on hover)
- [ ] Real-time updates show pulse skeleton or brief highlight animation
- [ ] Touch targets are ≥44×44px on mobile
- [ ] Focus rings are visible (2px outline)
- [ ] Animations use GSAP for smooth 60fps (not CSS for complex timing)
- [ ] Responsive tested: 375px (mobile), 768px (tablet), 1024px (desktop)
- [ ] All numbers/percentages include units and color coding (green/red)

## Use Cases

Use `/frontend-design` when:
- "Design a stock price card"
- "Create a 14-day tracking table"
- "Build a prediction confidence indicator"
- "Design a broker customer dashboard"
- "Create a WhatsApp lead card"
- "Build a real-time price ticker"

## Examples

### Stock Card (ASX Dashboard)
```
Border: 1px solid --color-border, 8px radius
Background: --color-surface (dark)
Layout: Symbol | Price (mono font) | Change % (color-coded) | Prediction badge
Hover: Border brightens, shadow grows, scale(1.01)
Touch: Card is full-width clickable (min 64px tall)
Data: BHP.ASX | $45.32 | +2.1% ↑ (green) | Meets target ✓
```

### Prediction Confidence Badge
```
Base: Filled rounded pill
Color: Green (#10B981) if confidence > 70%, Amber if 50-70%, Red if < 50%
Text: "92% confidence" (monospace, white text)
Icon: Checkmark/warning/alert paired with color
Animation: Slide-in on first render + subtle pulse on mount
```

### Real-time Price Update
```
When price changes:
1. Show old value with strikethrough (200ms fade)
2. New value slides up + pulse highlight (accent color, 300ms)
3. Arrow indicator (↑ green or ↓ red) animates scale 0→1 (150ms)
4. After 1s, fade back to normal state
```

### Prediction Chart Update (GSAP)
```
When new data point arrives:
1. Previous path stays visible
2. New curve segment animates in (cubic-bezier ease)
3. New point animates with scale + glow effect
4. Tooltip auto-reveals for 2s before fading
Duration: 300-500ms total for smooth feel
```

## Remember

- **Design is invisible when it works** — investors notice bad design more than good
- **Data clarity > aesthetic** — every pixel serves function
- **Real-time responsiveness** — animations should feel instant (< 300ms)
- **Professional ≠ boring** — use subtle micro-interactions to build confidence
- **Test on low-end devices** — trading happens everywhere; ensure 60fps smoothness
- **Color + symbol redundancy** — never rely on color alone for critical signals
