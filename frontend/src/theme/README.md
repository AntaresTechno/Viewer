# Frontend theme architecture

The application exposes one semantic `--app-*` contract and keeps design-system
implementations behind it.

- `foundation.css` owns the semantic contract, shared motion primitives and
  accessibility fallbacks. It must not contain page or vendor selectors.
- `miuix.css` and `md3e.css` implement the contract. They may define the legacy
  `--m-*` color roles required by existing pages and `miuix-vue`, but must not
  target a feature page.
- `adapters/miuix-vue.css` is the only file allowed to target `.m-*` component
  classes. This keeps the vendor dependency replaceable.
- Pages and layouts consume `--app-*` tokens for new work. A page-specific visual
  may expose a semantic token from a theme, but the theme must not target that page.

## Motion rules

- Give feedback on pointer down with `:active`.
- Use `--app-ease-calm` for navigation and effects; reserve
  `--app-ease-spring` for direct manipulation and expressive state changes.
- Animate `transform` and `opacity` where possible and preserve interruption.
- Every spatial transition needs a `prefers-reduced-motion` alternative.
- Translucent surfaces need a `prefers-reduced-transparency` solid fallback.

## Adding a theme

1. Add a new implementation file scoped by `html[data-design="..."]`.
2. Implement the semantic contract without importing a page or component.
3. Add vendor-specific shape or state mappings only in `adapters/`.
4. Import the implementation from `design.css` and extend `DesignId` in
   `stores/theme.ts`.
