# Scoped Animate UI integration review

Read-only review on 2026-10-05 of App/main/styles, vendored Animate UI tabs/dialog/tooltip/button/motion primitives, reader/progress, graph integration, API types and Git runtime exclusions. No browser, Ollama, broad test suite, new Claude invocation, source edits or commits were performed.

## Actionable findings

### P1 — Modal overlay stacks above both evidence and full-document drawers

`frontend/src/styles.css` retains `.source-drawer { z-index: 11; }`; the final drawer override does not change that value. The new vendored `components/animate-ui/components/radix/dialog/index.tsx` automatically renders `DialogOverlay` with `z-50`. The source and full-document drawer both use `.source-drawer`. The old `.drawer-overlay` with z-index 10 is no longer the rendered overlay.

The overlay therefore stacks above the drawer, covering its contents and intercepting pointer events. Custom unlayered CSS wins over the drawer's Tailwind z-50 utility, so the component's default content class does not repair the inherited z-index 11. Set an explicit coherent modal stack, such as overlay z-index 50 and drawer z-index 51. Check both dialogs rather than merely the component DOM rendering; CSS-less unit tests cannot catch this integration fault. Sent to root/frontend.

### P2 — Chat auto-scroll ignores reduced-motion preference

`frontend/src/App.tsx` messages/status effect calls `scrollIntoView({behavior:"smooth"})` on every message/token/status update. `MotionConfig reducedMotion="user"` covers Motion components, and CSS `scroll-behavior:auto !important` covers CSS-based scrolling, but an explicit JavaScript smooth-scroll option still requests animated scrolling.

Use `useReducedMotion()` and pass `behavior: reduceMotion ? "auto" : "smooth"`. Graph search/zoom/reset already apply zero duration for reduced motion, and the vendored button/dialog/tab/auto-height integrations honor the preference. Sent to root/frontend.

## Checked integration

Root integration resolution: both findings are fixed. The final .source-drawer override uses z-index 51, above the official overlay at 50. App uses useReducedMotion and chooses auto scrolling when requested. Final automated gates passed 37 Python and 32 frontend tests, lint/typecheck and build. This is source and automated verification; browser tool access remained blocked despite user authorization.

Desktop panels both remain mounted with forceMount; desktop CSS explicitly displays the inactive panel, while <=1099px uses the active panel only. Mobile main/panel min-height/min-width and flex rules preserve scroll areas; the responsive sidebar has a close control. DialogClose and TooltipTrigger compose Buttons with asChild, avoiding nested button markup. Dialog titles/descriptions and Radix focus/escape behavior remain wired. Reader refresh now depends on live document hash/status/stage/count/error revision with effect invalidation; it preserves matching document content during refresh. This resolves the preceding stale-reader finding.

Progress uses real backend phase/count data and accessible progress values. Source and full-document API consumers retain live-source fetch/error handling, safe HTTP(S) links, full-text form/provenance note and request invalidation. Graph remains evidence-backed and reads the documented API payload. Dynamic Sigma loading and the bundle-size advisory do not justify speculative manual chunking.

`data/`, `.env`, work/, build artifacts, node_modules and caches remain ignored; no runtime data paths appeared in Git status or tracked data listing. This review did not inspect private runtime contents.
