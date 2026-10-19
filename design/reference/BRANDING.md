# Torinteo Markets branding reference

Inspected 19 October 2026 from the owner's canonical Torinteo AI repository,
`Torinteo-ai/Torinteo-ai` (repository ID `1234906717`), at main tree
`a60ab164576c3837c42f5fa5f9ac82bd07a16f10`. This is reference research only;
the canonical application was not modified.

## Original TT mark

`web/public/branding/torinteo-logo.svg` is an exact copy of the original
`public/branding/torinteo-logo.svg`, Git blob
`4fce85635d940e42954a6e5ef56452c4a4242174` (779 bytes). The same blob supplies
the canonical app's navbar and dashboard logo SVGs. Its three paths match
`components/branding/logo.tsx` and `lib/branding/logo-geometry.ts`.

Preserve its geometry, transparent background, `0 0 1536 1385` viewBox,
and original fill `#20E6FF`. Render using contain sizing without an invented
badge, recolour, redraw, crop or surrounding logo container treatment.

The logo is the owner's custom Torinteo brand asset, reused for their Markets
product. No separate logo licence file was found in the inspected repository;
this copy does not grant a general third-party trademark or artwork licence.
No unrelated private app source or font binaries were copied.

## Visual system to implement independently

- Font stack: Roboto, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI,
  sans-serif. The inspected CSS declares this stack; no font binary was copied.
- App background: black / midnight blue, with `#02070d`, `#030b13` and `#020611`
  among the declared shell colours. Cyan is reserved for accents.
- Panels: `#07101b` / `#07111d`, restrained borders such as
  `rgba(103,232,249,.16)` and subtle inset highlights.
- UI cyan: `#22d3ee`; softer accents `#67e8f9`; the logo itself stays `#20E6FF`.
- Text: primary `#f8fafc`, secondary `#cbd5e1`, muted `#64748b`.
- Desktop shell: fixed 288px left sidebar, plain TT logo and compact wordmark,
  grouped icon-plus-label navigation, slim cyan active edge and low-opacity
  active background, account/workspace information at the bottom.
- Mobile shell: compact sticky header, horizontally scrollable navigation,
  safe-area padding, visible active item. Avoid clipping page content or inputs.
- Titles sit directly on the page background. Keep decorative treatment on
  functional content panels, not a separate large title card.
- Current source includes both glass panels and newer flattened surfaces.
  Reuse the palette, hierarchy and compact navigation; use modest radii and
  effects consistently instead of duplicating those accumulated CSS overrides.

Reference paths inspected: `tailwind.config.ts`, `styles/globals.css`,
`styles/flat-ui.css`, `styles/dashboard-dark-surface-parity.css`,
`styles/app-switcher-colour-parity.css`, `components/layout/app-shell.tsx`,
`components/branding/logo.tsx` and `lib/branding/config.ts`.

The public website was reachable through search retrieval, but an exact live
asset delivery check did not complete. The saved SVG is verified against the
canonical GitHub blob, not claimed to be a new production deployment check.
