/**
 * Save / update dialog for the Strategy Builder.
 *
 * Two modes:
 *   - create: collect a name, post via createStrategy()
 *   - update: pre-fill name + notes, put via updateStrategy(id)
 *
 * The trading mode (live/sandbox) is sourced from the page rather than
 * asked for here — it follows OpenBull's global mode toggle, matching
 * the Phase 1 design decision to drop OpenAlgo's dual-watchlist concept.
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { createStrategy, updateStrategy } from "@/api/strategies";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type {
  Strategy,
  StrategyLeg,
  StrategyMode,
} from "@/types/strategy";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** When set, the dialog updates this strategy instead of creating a new one. */
  existingId: number | null;
  /** Pre-fill values used in both create and update flows. */
  initialName?: string;
  initialNotes?: string | null;
  /** Strategy spec to persist. */
  underlying: string;
  exchange: string;
  expiryDate: string | null;
  legs: StrategyLeg[];
  mode: StrategyMode;
  onSaved: (strategy: Strategy) => void;
}

export function SaveStrategyDialog({
  open,
  onOpenChange,
  existingId,
  initialName = "",
  initialNotes = null,
  underlying,
  exchange,
  expiryDate,
  legs,
  mode,
  onSaved,
}: Props) {
  const [name, setName] = useState(initialName);
  const [notes, setNotes] = useState(initialNotes ?? "");
  const [submitting, setSubmitting] = useState(false);

  // Reset form when the dialog re-opens against a different strategy.
  useEffect(() => {
    if (open) {
      setName(initialName);
      setNotes(initialNotes ?? "");
    }
  }, [open, initialName, initialNotes]);

  const handleSave = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      toast.error("Strategy name is required");
      return;
    }
    if (legs.length === 0) {
      toast.error("Add at least one leg before saving");
      return;
    }

    setSubmitting(true);
    try {
      const saved = existingId
        ? await updateStrategy(existingId, {
            name: trimmed,
            underlying,
            exchange,
            expiry_date: expiryDate,
            legs,
            notes: notes.trim() || null,
          })
        : await createStrategy({
            name: trimmed,
            underlying,
            exchange,
            expiry_date: expiryDate,
            mode,
            legs,
            notes: notes.trim() || null,
          });
      toast.success(
        existingId ? "Strategy updated" : `Strategy '${saved.name}' saved`,
      );
      onSaved(saved);
      onOpenChange(false);
    } catch (e) {
      const msg =
        (e as { response?: { data?: { detail?: string } }; message?: string })
          ?.response?.data?.detail ??
        (e as { message?: string })?.message ??
        "Failed to save strategy";
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {existingId ? "Update strategy" : "Save strategy"}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="strategy-name">Name</Label>
            <Input
              id="strategy-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. NIFTY Iron Condor 02-MAY"
              autoFocus
              maxLength={200}
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="strategy-notes">Notes (optional)</Label>
            <textarea
              id="strategy-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Why this trade, exit plan, etc."
              rows={3}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
            />
          </div>

          <p className="text-xs text-muted-foreground">
            Saving as <span className="font-medium">{mode}</span> mode.
            <br />
            {legs.length} leg{legs.length === 1 ? "" : "s"} on {underlying}{" "}
            {expiryDate ? `(${expiryDate})` : ""}.
          </p>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={submitting}>
            {submitting
              ? "Saving…"
              : existingId
                ? "Update"
                : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
