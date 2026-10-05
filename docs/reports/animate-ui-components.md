# Animate UI component vendoring

The UI uses actual official Animate UI source, vendored from [imskyleen/animate-ui](https://github.com/imskyleen/animate-ui) at commit `efeb96ffd7a3b7a4868667e4ac3c346620fb3044`. Sources were fetched directly from GitHub over HTTPS after inspecting the repository tree and official [Dialog](https://animate-ui.com/docs/components/radix/dialog), [Tabs](https://animate-ui.com/docs/components/radix/tabs) and [Accordion](https://animate-ui.com/docs/components/radix/accordion) documentation. No registry CLI or downloaded script was executed.

Upstream's actual license is **MIT + Commons Clause License Condition**, copyright 2025 Elliot Sutton. It permits integrating these components into an application, website or product; it restricts selling or redistributing the original components alone or in a bundle. The complete unchanged upstream license is reproduced in `frontend/THIRD_PARTY_LICENSES.md`. Each vendored source file records its upstream commit and path. Radix UI is credited for accessible primitives and shadcn/ui for styling inspiration.

## Import contract

Application files in `frontend/src` import from `./components/animate-ui/components/…`:

- `buttons/button`: `Button`, `ButtonProps`, `buttonVariants`; variants default/accent/destructive/outline/secondary/ghost/link, sizes default/sm/lg/icon/icon-sm/icon-lg; standard Motion button props and `asChild`.
- `radix/dialog`: `Dialog`, `DialogTrigger`, `DialogClose`, `DialogContent`, `DialogHeader`, `DialogFooter`, `DialogTitle`, `DialogDescription`. Controlled `open`/`onOpenChange`, modal focus management and Escape handling come from Radix. `DialogContent` has `showCloseButton` (default true), supports Motion props, and excludes `asChild`/`forceMount` because its internal primitive owns animation.
- `radix/tabs`: `Tabs`, `TabsList`, `TabsTrigger`, `TabsContent`, `TabsContents`; Radix values and keyboard semantics, animated highlight and content sizing.
- `radix/progress`: `Progress`; Radix root props including `value`, `max`, standard `aria-label` and `getValueLabel`. Null is indeterminate. Raw counts remain accessible (for example 2/5); only the visual indicator is normalized to 40%.
- `radix/tooltip`: `Tooltip`, `TooltipProvider`, `TooltipTrigger`, `TooltipContent`.
- `radix/accordion`: `Accordion`, `AccordionItem`, `AccordionTrigger`, `AccordionContent`; Radix single/multiple type and controlled-value contracts.

Dependencies required by the actual source: `react`, `motion`, `radix-ui` (the unified package), `lucide-react`, `class-variance-authority`, `clsx`, `tailwind-merge`. Installation/configuration is owned by the integration agent. `frontend/src/lib/utils.ts` supplies the small local `cn` helper using clsx and tailwind-merge.

## Retained source and adaptations

The six styled components, their six primitives and six required helpers are retained. Registry aliases were changed to relative imports; unused React namespace imports were removed under the automatic JSX runtime, and `WithAsChild` became a type-only import. The styled TooltipProvider is additionally exported as a local convenience; upstream defined it internally. No substitute wrapper is labeled as official Animate UI.

Upstream progress treated all values as percentages. The local primitive now normalizes against `max` for its visual transform while preserving Radix's raw value/max semantics. Undefined/null value remains indeterminate. This is documented as a local correctness fix, not attributed to upstream.

Button, Dialog, Tabs, Accordion, Tooltip, Progress and AutoHeight use Motion's `useReducedMotion` to eliminate scaling, blur and animation duration when requested. The application root should additionally use `<MotionConfig reducedMotion="user">` to suppress layout/transform motion throughout the tree, including Highlight. No claim about visual appearance or browser rendering is made by source vendoring alone.

Vendored upstream paths (each local relative path mirrors the path beneath `apps/www/registry/`):

- `apps/www/registry/components/radix/dialog/index.tsx`
- `apps/www/registry/components/radix/tabs/index.tsx`
- `apps/www/registry/components/radix/progress/index.tsx`
- `apps/www/registry/components/radix/tooltip/index.tsx`
- `apps/www/registry/components/radix/accordion/index.tsx`
- `apps/www/registry/primitives/radix/dialog/index.tsx`
- `apps/www/registry/primitives/radix/tabs/index.tsx`
- `apps/www/registry/primitives/radix/progress/index.tsx`
- `apps/www/registry/primitives/radix/tooltip/index.tsx`
- `apps/www/registry/primitives/radix/accordion/index.tsx`
- `apps/www/registry/components/buttons/button/index.tsx`
- `apps/www/registry/primitives/buttons/button/index.tsx`
- `apps/www/registry/hooks/use-controlled-state/index.tsx`
- `apps/www/registry/lib/get-strict-context/index.tsx`
- `apps/www/registry/primitives/effects/highlight/index.tsx`
- `apps/www/registry/primitives/effects/auto-height/index.tsx`
- `apps/www/registry/primitives/animate/slot/index.tsx`
- `apps/www/registry/hooks/use-auto-height/index.tsx`

## Verification

`npm run check` passed after integration dependencies were available. Scoped `npx eslint src/components/animate-ui src/lib` passed. Targeted `npx vitest run src/Progress.test.tsx src/DocumentReader.test.tsx src/Evidence.test.tsx` passed: three files, nine tests. These verify interactions and progress semantics; they do not establish visual quality.
