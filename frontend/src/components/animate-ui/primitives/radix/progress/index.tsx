// Vendored from Animate UI (MIT + Commons Clause), Elliot Sutton / imskyleen.
// Upstream: efeb96ffd7a3b7a4868667e4ac3c346620fb3044 — apps/www/registry/primitives/radix/progress/index.tsx
"use client";

import * as React from "react";
import { Progress as ProgressPrimitive } from "radix-ui";
import { motion, useReducedMotion } from "motion/react";

import { getStrictContext } from "../../../lib/get-strict-context/index";

type ProgressContextType = {
  value: number | null;
  max: number;
};

const [ProgressProvider, useProgress] =
  getStrictContext<ProgressContextType>("ProgressContext");

type ProgressProps = React.ComponentProps<typeof ProgressPrimitive.Root>;

function Progress(props: ProgressProps) {
  return (
    <ProgressProvider
      value={{ value: props.value ?? null, max: props.max ?? 100 }}
    >
      <ProgressPrimitive.Root data-slot="progress" {...props} />
    </ProgressProvider>
  );
}

const MotionProgressIndicator = motion.create(ProgressPrimitive.Indicator);

type ProgressIndicatorProps = React.ComponentProps<
  typeof MotionProgressIndicator
>;

function ProgressIndicator({
  transition = { type: "spring", stiffness: 100, damping: 30 },
  ...props
}: ProgressIndicatorProps) {
  const { value, max } = useProgress();
  const reduceMotion = useReducedMotion();
  const percentage =
    value === null
      ? null
      : Math.min(100, Math.max(0, (value / (max > 0 ? max : 100)) * 100));

  return (
    <MotionProgressIndicator
      data-slot="progress-indicator"
      data-indeterminate={percentage === null || undefined}
      animate={{ x: percentage === null ? "-100%" : `-${100 - percentage}%` }}
      transition={reduceMotion ? { duration: 0 } : transition}
      {...props}
    />
  );
}

export {
  Progress,
  ProgressIndicator,
  useProgress,
  type ProgressProps,
  type ProgressIndicatorProps,
  type ProgressContextType,
};
