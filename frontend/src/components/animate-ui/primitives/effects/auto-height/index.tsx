// Vendored from Animate UI (MIT + Commons Clause), Elliot Sutton / imskyleen.
// Upstream: efeb96ffd7a3b7a4868667e4ac3c346620fb3044 — apps/www/registry/primitives/effects/auto-height/index.tsx
"use client";

import * as React from "react";
import {
  motion,
  useReducedMotion,
  type HTMLMotionProps,
  type LegacyAnimationControls,
  type TargetAndTransition,
  type Transition,
} from "motion/react";

import { useAutoHeight } from "../../../hooks/use-auto-height/index";
import { Slot, type WithAsChild } from "../../animate/slot/index";

type AutoHeightProps = WithAsChild<
  {
    children: React.ReactNode;
    deps?: React.DependencyList;
    animate?: TargetAndTransition | LegacyAnimationControls;
    transition?: Transition;
  } & Omit<HTMLMotionProps<"div">, "animate">
>;

function AutoHeight({
  children,
  deps = [],
  transition = {
    type: "spring",
    stiffness: 300,
    damping: 30,
    bounce: 0,
    restDelta: 0.01,
  },
  style,
  animate,
  asChild = false,
  ...props
}: AutoHeightProps) {
  const reduceMotion = useReducedMotion();
  const { ref, height } = useAutoHeight<HTMLDivElement>(deps);

  const Comp = asChild ? Slot : motion.div;

  return (
    <Comp
      style={{ overflow: "hidden", ...style }}
      animate={{ height, ...animate }}
      transition={reduceMotion ? { duration: 0 } : transition}
      {...props}
    >
      <div ref={ref}>{children}</div>
    </Comp>
  );
}

export { AutoHeight, type AutoHeightProps };
