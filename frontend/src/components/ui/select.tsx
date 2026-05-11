/**
 * shadcn-API-compatible Select built on @base-ui/react.
 *
 * Provides the same surface openalgo's playground uses
 * (Select, SelectTrigger, SelectValue, SelectContent, SelectGroup,
 * SelectLabel, SelectItem) so ported code lands without changes.
 */
import { Select as SelectPrimitive } from "@base-ui/react/select";
import { ChevronDown, Check } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

interface SelectProps {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  disabled?: boolean;
  children: ReactNode;
}

export function Select({
  value,
  defaultValue,
  onValueChange,
  disabled,
  children,
}: SelectProps) {
  return (
    <SelectPrimitive.Root
      value={value}
      defaultValue={defaultValue}
      onValueChange={onValueChange}
      disabled={disabled}
    >
      {children}
    </SelectPrimitive.Root>
  );
}

interface SelectTriggerProps {
  className?: string;
  children: ReactNode;
}

export function SelectTrigger({ className, children }: SelectTriggerProps) {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        "flex h-9 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm ring-offset-background data-[placeholder]:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        "[&>span]:line-clamp-1",
        className,
      )}
    >
      <span className="flex-1 truncate text-left">{children}</span>
      <SelectPrimitive.Icon className="ml-2 opacity-50">
        <ChevronDown className="h-4 w-4" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

interface SelectValueProps {
  placeholder?: string;
}

export function SelectValue({ placeholder }: SelectValueProps) {
  return <SelectPrimitive.Value>{placeholder}</SelectPrimitive.Value>;
}

interface SelectContentProps {
  className?: string;
  children: ReactNode;
}

export function SelectContent({ className, children }: SelectContentProps) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Positioner sideOffset={4} className="z-50">
        <SelectPrimitive.Popup
          className={cn(
            "max-h-[var(--available-height)] overflow-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md",
            "data-[ending-style]:opacity-0 data-[starting-style]:opacity-0",
            "transition-opacity duration-100",
            "min-w-[var(--anchor-width)]",
            className,
          )}
        >
          <SelectPrimitive.List>{children}</SelectPrimitive.List>
        </SelectPrimitive.Popup>
      </SelectPrimitive.Positioner>
    </SelectPrimitive.Portal>
  );
}

interface SelectGroupProps {
  children: ReactNode;
}

export function SelectGroup({ children }: SelectGroupProps) {
  return <SelectPrimitive.Group>{children}</SelectPrimitive.Group>;
}

interface SelectLabelProps {
  className?: string;
  children: ReactNode;
}

export function SelectLabel({ className, children }: SelectLabelProps) {
  return (
    <SelectPrimitive.GroupLabel
      className={cn("px-2 py-1.5 text-sm font-semibold", className)}
    >
      {children}
    </SelectPrimitive.GroupLabel>
  );
}

interface SelectItemProps {
  value: string;
  className?: string;
  children: ReactNode;
}

export function SelectItem({ value, className, children }: SelectItemProps) {
  return (
    <SelectPrimitive.Item
      value={value}
      className={cn(
        "relative flex w-full cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-sm outline-none",
        "data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground",
        "data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
        className,
      )}
    >
      <SelectPrimitive.ItemIndicator className="absolute left-2 flex h-4 w-4 items-center justify-center">
        <Check className="h-3 w-3" />
      </SelectPrimitive.ItemIndicator>
      <SelectPrimitive.ItemText className="flex-1 truncate">
        {children}
      </SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
}
