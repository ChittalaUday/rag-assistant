import "@testing-library/jest-dom/vitest";

// JSDOM has no layout observer; Radix tooltip sizing requires the browser API.
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// JSDOM lacks PointerEvent; Motion uses it for keyboard-generated press events.
if (!globalThis.PointerEvent) {
  class TestPointerEvent extends MouseEvent {
    readonly pointerId: number;
    readonly pointerType: string;
    readonly isPrimary: boolean;
    constructor(type: string, options: PointerEventInit = {}) {
      super(type, options);
      this.pointerId = options.pointerId ?? 0;
      this.pointerType = options.pointerType ?? "mouse";
      this.isPrimary = options.isPrimary ?? true;
    }
  }
  Object.defineProperty(globalThis, "PointerEvent", {
    value: TestPointerEvent,
  });
}

// Dialog scroll restoration is a browser operation with no layout in JSDOM.
Object.defineProperty(window, "scrollTo", { value: () => {}, writable: true });
