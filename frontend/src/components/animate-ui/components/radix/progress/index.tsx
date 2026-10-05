// Vendored from Animate UI (MIT + Commons Clause), Elliot Sutton / imskyleen.
// Upstream: efeb96ffd7a3b7a4868667e4ac3c346620fb3044 — apps/www/registry/components/radix/progress/index.tsx

import {
  Progress as ProgressPrimitive,
  ProgressIndicator as ProgressIndicatorPrimitive,
  type ProgressProps as ProgressPrimitiveProps,
} from "../../../primitives/radix/progress/index";
import { cn } from "../../../../../lib/utils";

type ProgressProps = ProgressPrimitiveProps;

function Progress({ className, ...props }: ProgressProps) {
  return (
    <ProgressPrimitive
      className={cn(
        "bg-primary/20 relative h-2 w-full overflow-hidden rounded-full",
        className,
      )}
      {...props}
    >
      <ProgressIndicatorPrimitive className="bg-primary rounded-full h-full w-full flex-1" />
    </ProgressPrimitive>
  );
}

export { Progress, type ProgressProps };
