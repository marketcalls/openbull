/**
 * Templates dropdown — click-to-fill the legs from a preset.
 *
 * Uses a plain `<select>` grouped by `<optgroup>` rather than a custom
 * popover so it inherits OpenBull's existing select styling and works
 * on mobile without extra glue. The actual template→legs resolution
 * (with strike-offset → absolute strike via the available chain)
 * happens in the parent page so it can plug in the current ATM and
 * strike grid.
 */

import { templatesByCategory, type StrategyTemplate } from "@/lib/strategyTemplates";

interface Props {
  /** Currently-selected template id, or empty when the user is in
   *  custom-build mode. */
  value: string;
  onApply: (template: StrategyTemplate) => void;
  disabled?: boolean;
}

const CATEGORY_LABELS: Record<StrategyTemplate["category"], string> = {
  bullish: "Bullish",
  bearish: "Bearish",
  neutral: "Neutral",
  volatility: "Volatility",
  calendar: "Calendar / Diagonal",
};

export function StrategyTemplatePicker({ value, onApply, disabled }: Props) {
  const grouped = templatesByCategory();

  return (
    <div className="space-y-1">
      <label className="block text-xs text-muted-foreground">Template</label>
      <select
        value={value}
        onChange={(e) => {
          const id = e.target.value;
          if (!id) return;
          const tpl = Object.values(grouped)
            .flat()
            .find((t) => t.id === id);
          if (tpl) onApply(tpl);
        }}
        disabled={disabled}
        className="h-8 w-44 rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
      >
        <option value="">— Pick a template —</option>
        {(Object.keys(grouped) as Array<StrategyTemplate["category"]>).map(
          (category) => {
            const items = grouped[category];
            if (items.length === 0) return null;
            return (
              <optgroup key={category} label={CATEGORY_LABELS[category]}>
                {items.map((t) => (
                  <option key={t.id} value={t.id} title={t.description}>
                    {t.name}
                  </option>
                ))}
              </optgroup>
            );
          },
        )}
      </select>
    </div>
  );
}
