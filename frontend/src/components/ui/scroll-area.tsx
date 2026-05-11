/**
 * shadcn-API-compatible ScrollArea built on @base-ui/react.
 *
 * openalgo's playground uses ``<ScrollArea>{children}</ScrollArea>`` as a
 * flex container that scrolls vertically. We map that to Base UI's
 * scroll-area parts so the visual is identical (with a styled scrollbar).
 */
import { ScrollArea as ScrollAreaPrimitive } from "@base-ui/react/scroll-area";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface ScrollAreaProps {
  className?: string;
  children: ReactNode;
}

export function ScrollArea({ className, children }: ScrollAreaProps) {
  return (
    <ScrollAreaPrimitive.Root className={cn("relative overflow-hidden", className)}>
      <ScrollAreaPrimitive.Viewport className="h-full w-full">
        {children}
      </ScrollAreaPrimitive.Viewport>
      <ScrollAreaPrimitive.Scrollbar
        orientation="vertical"
        className="flex w-2 touch-none select-none bg-transparent p-px transition-colors hover:bg-muted/30"
      >
        <ScrollAreaPrimitive.Thumb className="relative flex-1 rounded-full bg-muted-foreground/30 hover:bg-muted-foreground/50" />
      </ScrollAreaPrimitive.Scrollbar>
    </ScrollAreaPrimitive.Root>
  );
}
