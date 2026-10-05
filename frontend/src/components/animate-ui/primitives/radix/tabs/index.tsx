// Vendored from Animate UI (MIT + Commons Clause), Elliot Sutton / imskyleen.
// Upstream: efeb96ffd7a3b7a4868667e4ac3c346620fb3044 — apps/www/registry/primitives/radix/tabs/index.tsx
"use client";

import * as React from "react";
import { Tabs as TabsPrimitive } from "radix-ui";
import {
  motion,
  useReducedMotion,
  AnimatePresence,
  type HTMLMotionProps,
  type Transition,
} from "motion/react";

import {
  Highlight,
  HighlightItem,
  type HighlightProps,
  type HighlightItemProps,
} from "../../effects/highlight/index";
import { getStrictContext } from "../../../lib/get-strict-context/index";
import { useControlledState } from "../../../hooks/use-controlled-state/index";
import {
  AutoHeight,
  type AutoHeightProps,
} from "../../effects/auto-height/index";

type TabsContextType = {
  value: string | undefined;
  setValue: TabsProps["onValueChange"];
};

const [TabsProvider, useTabs] =
  getStrictContext<TabsContextType>("TabsContext");

type TabsProps = React.ComponentProps<typeof TabsPrimitive.Root>;

function Tabs(props: TabsProps) {
  const [value, setValue] = useControlledState({
    value: props.value,
    defaultValue: props.defaultValue,
    onChange: props.onValueChange,
  });

  return (
    <TabsProvider value={{ value, setValue }}>
      <TabsPrimitive.Root
        data-slot="tabs"
        {...props}
        onValueChange={setValue}
      />
    </TabsProvider>
  );
}

type TabsHighlightProps = Omit<HighlightProps, "controlledItems" | "value">;

function TabsHighlight({
  transition = { type: "spring", stiffness: 200, damping: 25 },
  ...props
}: TabsHighlightProps) {
  const reduceMotion = useReducedMotion();
  const { value } = useTabs();

  return (
    <Highlight
      data-slot="tabs-highlight"
      controlledItems
      value={value}
      transition={reduceMotion ? { duration: 0 } : transition}
      click={false}
      {...props}
    />
  );
}

type TabsListProps = React.ComponentProps<typeof TabsPrimitive.List>;

function TabsList(props: TabsListProps) {
  return <TabsPrimitive.List data-slot="tabs-list" {...props} />;
}

type TabsHighlightItemProps = HighlightItemProps & {
  value: string;
};

function TabsHighlightItem(props: TabsHighlightItemProps) {
  return <HighlightItem data-slot="tabs-highlight-item" {...props} />;
}

type TabsTriggerProps = React.ComponentProps<typeof TabsPrimitive.Trigger>;

function TabsTrigger(props: TabsTriggerProps) {
  return <TabsPrimitive.Trigger data-slot="tabs-trigger" {...props} />;
}

type TabsContentProps = React.ComponentProps<typeof TabsPrimitive.Content> &
  HTMLMotionProps<"div">;

function TabsContent({
  value,
  forceMount,
  transition = { duration: 0.5, ease: "easeInOut" },
  ...props
}: TabsContentProps) {
  const reduceMotion = useReducedMotion();
  return (
    <AnimatePresence mode="wait">
      <TabsPrimitive.Content asChild forceMount={forceMount} value={value}>
        <motion.div
          data-slot="tabs-content"
          layout
          layoutDependency={value}
          initial={{ opacity: 0, filter: reduceMotion ? "none" : "blur(4px)" }}
          animate={{ opacity: 1, filter: reduceMotion ? "none" : "blur(0px)" }}
          exit={{ opacity: 0, filter: reduceMotion ? "none" : "blur(4px)" }}
          transition={reduceMotion ? { duration: 0 } : transition}
          {...props}
        />
      </TabsPrimitive.Content>
    </AnimatePresence>
  );
}

type TabsContentsAutoProps = AutoHeightProps & {
  mode?: "auto-height";
  children: React.ReactNode;
  transition?: Transition;
};

type TabsContentsLayoutProps = Omit<HTMLMotionProps<"div">, "transition"> & {
  mode: "layout";
  children: React.ReactNode;
  transition?: Transition;
};

type TabsContentsProps = TabsContentsAutoProps | TabsContentsLayoutProps;

const defaultTransition: Transition = {
  type: "spring",
  stiffness: 200,
  damping: 30,
};

function isAutoMode(props: TabsContentsProps): props is TabsContentsAutoProps {
  return !("mode" in props) || props.mode === "auto-height";
}

function TabsContents(props: TabsContentsProps) {
  const reduceMotion = useReducedMotion();
  const { value } = useTabs();

  if (isAutoMode(props)) {
    const { transition = defaultTransition, ...autoProps } = props;

    return (
      <AutoHeight
        data-slot="tabs-contents"
        deps={[value]}
        transition={reduceMotion ? { duration: 0 } : transition}
        {...autoProps}
      />
    );
  }

  const { transition = defaultTransition, style, ...layoutProps } = props;

  return (
    <motion.div
      data-slot="tabs-contents"
      layout="size"
      layoutDependency={value}
      style={{ overflow: "hidden", ...style }}
      transition={{ layout: reduceMotion ? { duration: 0 } : transition }}
      {...layoutProps}
    />
  );
}

export {
  Tabs,
  TabsHighlight,
  TabsHighlightItem,
  TabsList,
  TabsTrigger,
  TabsContent,
  TabsContents,
  type TabsProps,
  type TabsHighlightProps,
  type TabsHighlightItemProps,
  type TabsListProps,
  type TabsTriggerProps,
  type TabsContentProps,
  type TabsContentsProps,
};
