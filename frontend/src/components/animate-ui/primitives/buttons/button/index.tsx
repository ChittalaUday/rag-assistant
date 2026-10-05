// Vendored from Animate UI (MIT + Commons Clause), Elliot Sutton / imskyleen.
// Upstream: efeb96ffd7a3b7a4868667e4ac3c346620fb3044 — apps/www/registry/primitives/buttons/button/index.tsx
"use client";

import { motion, useReducedMotion, type HTMLMotionProps } from "motion/react";

import { Slot, type WithAsChild } from "../../animate/slot/index";

type ButtonProps = WithAsChild<
  HTMLMotionProps<"button"> & {
    hoverScale?: number;
    tapScale?: number;
  }
>;

function Button({
  hoverScale = 1.05,
  tapScale = 0.95,
  asChild = false,
  ...props
}: ButtonProps) {
  const Component = asChild ? Slot : motion.button;
  const reduceMotion = useReducedMotion();

  return (
    <Component
      whileTap={{ scale: reduceMotion ? 1 : tapScale }}
      whileHover={{ scale: reduceMotion ? 1 : hoverScale }}
      {...props}
    />
  );
}

export { Button, type ButtonProps };
