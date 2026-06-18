---
name: ui-design-system
description: Build consistent ASX dashboard design systems with component tokens, typography scales, data-driven spacing, and financial accessibility. Every component is documented, reusable, and themeable.
user-invokable: true
tools: ["read_file", "replace_string_in_file", "create_file", "grep_search"]
---

# UI Design System Skill - ASX Prediction Platform

## Purpose
Ensure visual and functional consistency across all dashboards (ASX frontend, Broker frontend, n8n dashboard) using a single source of truth. Define tokens once, apply everywhere, audit quality at scale.

## Design Tokens - ASX Prediction

### Color Tokens

```
Brand & Trust:
  --color-primary: #1E40AF (Professional Blue)
  --color-primary-light: #3B82F6
  --color-primary-dark: #1E3A8A
  
Sentiment & Performance:
  --color-success: #10B981 (Emerald) — gains, bullish
  --color-success-light: #6EE7B7
  --color-success-dark: #059669
  
  --color-danger: #EF4444 (Red) — losses, bearish
  --color-danger-light: #FCA5A5
  --color-danger-dark: #DC2626
  
  --color-warning: #F59E0B (Amber) — volatility, caution
  --color-warning-light: #FCD34D
  --color-warning-dark: #D97706
  
  --color-info: #0EA5E9 (Sky Blue) — neutral info
  --color-info-light: #38BDF8
  --color-info-dark: #0369A1

Neutrals (Dark Dashboard):
  --color-bg: #0F172A (Deep slate base)
  --color-bg-secondary: #1A1F35
  --color-surface: #1E293B (Card/container background)
  --color-surface-hover: #334155
  --color-fg: #F1F5F9 (Primary text - light)
  --color-fg-secondary: #CBD5E1 (Secondary text)
  --color-fg-muted: #94A3B8 (Muted text, hints)
  --color-border: #334155 (Subtle dividers)
  --color-border-strong: #475569 (Emphasized borders)
  
Broker/Secondary Theme:
  --color-teal: #14B8A6 (Broker accent)
  --color-purple: #A855F7 (Alternative accent)
```

### Typography Tokens

```
Font Families:
  --font-sans: 'IBM Plex Sans', 'Roboto', 'Segoe UI', sans-serif
  --font-mono: 'IBM Plex Mono', 'JetBrains Mono', monospace
  --font-display: 'IBM Plex Sans', sans-serif (700 weight for headers)

Font Sizes:
  --text-xs: 12px (0.75rem) — timestamps, captions
  --text-sm: 14px (0.875rem) — labels, secondary data
  --text-base: 16px (1rem) — body text, prices
  --text-lg: 18px (1.125rem) — subheadings, key values
  --text-xl: 20px (1.25rem) — card titles
  --text-2xl: 24px (1.5rem) — section headings
  --text-3xl: 32px (2rem) — page titles
  --text-4xl: 40px (2.5rem) — hero metric

Font Weights:
  --fw-light: 300 (rare, only for hints)
  --fw-regular: 400 (body text)
  --fw-medium: 500 (labels, emphasis)
  --fw-semibold: 600 (subheadings, card titles)
  --fw-bold: 700 (headers, CTAs, important data)
  --fw-extrabold: 800 (hero metrics, big numbers)

Line Heights:
  --lh-tight: 1.2 (headers)
  --lh-normal: 1.5 (body, data rows)
  --lh-relaxed: 1.75 (longer descriptions)
```

### Spacing Tokens

```
4px base unit scale:
  --spacing-1: 4px (tight, internal)
  --spacing-2: 8px (padding inside components)
  --spacing-3: 12px (component gaps)
  --spacing-4: 16px (standard padding, margins)
  --spacing-5: 20px (section spacing)
  --spacing-6: 24px (larger sections)
  --spacing-8: 32px (major sections)
  --spacing-10: 40px (layout blocks)
  --spacing-12: 48px (hero spacing)
```

### Shadow Tokens (for depth on dark bg)

```
--shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3)
--shadow-md: 0 4px 6px rgba(0, 0, 0, 0.4)
--shadow-lg: 0 10px 15px rgba(0, 0, 0, 0.5)
--shadow-xl: 0 20px 25px rgba(0, 0, 0, 0.6)
--shadow-2xl: 0 25px 50px rgba(0, 0, 0, 0.7)
--shadow-inner: inset 0 2px 4px rgba(0, 0, 0, 0.3)
```

### Border Radius Tokens

```
--radius-sm: 4px (tight corners, inputs)
--radius-md: 6px (standard elements)
--radius-lg: 8px (cards, panels)
--radius-xl: 12px (modals, larger cards)
--radius-2xl: 16px (hero cards, badges)
--radius-full: 9999px (pills, avatars, buttons)
```

### Animation Tokens

```
Duration:
  --duration-fast: 150ms (hover states, micro-interactions)
  --duration-normal: 300ms (standard transitions, data updates)
  --duration-slow: 500ms (attention-seeking, major transitions)
  --duration-very-slow: 1000ms (page loads, hero animations)

Easing:
  --ease-linear: linear
  --ease-in: cubic-bezier(0.4, 0, 1, 1)
  --ease-out: cubic-bezier(0, 0, 0.2, 1)
  --ease-in-out: cubic-bezier(0.4, 0, 0.2, 1)
  --ease-spring: cubic-bezier(0.68, -0.55, 0.265, 1.55)
  --ease-bounce: cubic-bezier(0.68, -0.55, 0.265, 1.55)
```

## Component Library

### Button Component
```
States:
  - Base: 40px height, 16px horizontal padding (touch: min 44×44px)
  - Variants: primary (blue), secondary (ghost), danger (red), success (green)
  - Sizes: sm (36px), md (40px), lg (48px)
  - States: default, hover, active, disabled, loading, focus

Accessibility:
  - Focus ring visible (2px outline, primary color, 2px inset)
  - Min touch target: 44×44px
  - ARIA labels for icon buttons
  - Disabled state has 50% opacity but color still distinguishable
```

### Data Card Component
```
Structure:
  - Padding: 16px mobile → 20px tablet → 24px desktop
  - Border: 1px solid --color-border
  - Border radius: 8px
  - Background: --color-surface
  - Shadow: --shadow-md on idle, --shadow-lg on hover
  
Variants:
  - Default (neutral)
  - Success (green left border accent)
  - Danger (red left border accent)
  - Tracking (with progress bar)
  - Prediction (with confidence badge)
```

### Price Display Component
```
Structure:
  - Font: Monospace (IBM Plex Mono), size text-lg
  - Weight: 600 (semibold for readability)
  - Units: Always show currency ($, %) next to number
  - Change indicator: Color (green/red) + arrow (↑/↓)
  - Animation: Brief highlight pulse on update
  
Example: $45.32 ↑ +2.1%
  - $45.32: primary color, monospace
  - ↑: green, animated scale-in on update
  - +2.1%: green, same weight as price
```

### Chart Container
```
Structure:
  - Background: --color-surface
  - Padding: 16px
  - Border radius: 8px
  - Title: text-lg, semibold, --color-fg
  - Legend: text-sm, --color-fg-secondary
  - Aspect ratio: 16:9 (responsive)
  
Real-time updates:
  - New data point animates in with GSAP cubic-bezier
  - Tooltip reveals new values
  - No jarring resets
```

### Table Component
```
Header:
  - Background: --color-bg-secondary
  - Font: text-sm, 600, --color-fg-secondary
  - Padding: 12px 16px
  - Border-bottom: 1px solid --color-border

Rows:
  - Padding: 12px 16px (min height: 44px touch)
  - Border-bottom: 1px solid --color-border
  - Hover: Background to --color-surface-hover, cursor pointer
  
Data cells:
  - Monospace for numbers/prices
  - Color-coded for changes (green/red)
  - Left-aligned for text, right-aligned for numbers
```

### Badge/Pill Component
```
Variants:
  - Solid (colored bg, white text)
  - Outline (transparent, colored border)
  - Subtle (colored bg at 15% opacity)

Sizes:
  - sm: 12px text, 4px padding, 4px radius
  - md: 14px text, 6px padding, 6px radius
  - lg: 16px text, 8px padding, 8px radius

Uses: Status (Meeting/Not meeting), Prediction (Bullish/Bearish), Activity (New/Updated)
```

### Input Component
```
Structure:
  - Height: 40px
  - Padding: 10px 12px
  - Border: 1px solid --color-border
  - Border radius: 6px
  - Font: --text-base, monospace (for number fields)

States:
  - Focus: border → --color-primary, shadow subtle
  - Error: border → --color-danger
  - Disabled: opacity 50%, cursor not-allowed
  - Filled: subtle background change to --color-bg

Placeholder:
  - Color: --color-fg-muted
  - Text: Clear, action-oriented hints
```

## Responsive Breakpoints

```
Mobile:    < 640px   (default)
Tablet:    640px     @md breakpoint
Desktop:   1024px    @lg breakpoint
Large:     1280px    @xl breakpoint
Max-width: 1440px (optimal for dashboards)
```

## Accessibility Checklist

When creating components:

- [ ] **Color contrast**: Text ≥4.5:1 (WCAG AA), graphics ≥3:1
- [ ] **Focus indicators**: Always visible, minimum 2px outline
- [ ] **Touch targets**: Minimum 44×44px on mobile
- [ ] **Data clarity**: Color + symbol redundancy (never color alone)
- [ ] **Keyboard navigation**: Tab order logical, arrow keys for data tables
- [ ] **Screen readers**: ARIA labels on icons, semantic HTML, alt text on charts
- [ ] **Reduced motion**: Respect `prefers-reduced-motion`, disable animations
- [ ] **Number formatting**: Always include units (%, $, K, M)
- [ ] **Error states**: Clear messages, not just red borders
- [ ] **Loading states**: Skeleton screens instead of spinners, visual indication
