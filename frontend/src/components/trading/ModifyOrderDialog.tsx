/**
 * Modify-order dialog — surfaces editable quantity / price / pricetype /
 * trigger price for a single open order. Wires into POST /api/v1/modifyorder.
 *
 * Pricetype lock: most brokers only accept modify within compatible
 * pricetype families (LIMIT ↔ SL-L, MARKET ↔ SL-M). Switching across
 * families typically requires cancel + replace, so we let the backend's
 * 400 propagate as an error toast rather than pre-validating here.
 */

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { modifyOrder } from "@/api/orders";
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
import type { OrderbookItem } from "@/types/order";

const PRICE_TYPES = [
  { value: "MARKET", label: "Market" },
  { value: "LIMIT", label: "Limit" },
  { value: "SL", label: "SL-L" },
  { value: "SL-M", label: "SL-M" },
] as const;

type PriceType = (typeof PRICE_TYPES)[number]["value"];

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  order: OrderbookItem | null;
  onModified?: (orderid: string) => void;
}

export function ModifyOrderDialog({ open, onOpenChange, order, onModified }: Props) {
  const [quantity, setQuantity] = useState<number>(0);
  const [pricetype, setPricetype] = useState<PriceType>("MARKET");
  const [price, setPrice] = useState<number>(0);
  const [triggerPrice, setTriggerPrice] = useState<number>(0);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!order || !open) return;
    setQuantity(order.quantity);
    const ptUpper = (order.pricetype || "").toUpperCase() as PriceType;
    const valid = PRICE_TYPES.some((p) => p.value === ptUpper);
    setPricetype(valid ? ptUpper : "MARKET");
    setPrice(order.price ?? 0);
    setTriggerPrice(order.trigger_price ?? 0);
    setSubmitting(false);
  }, [order, open]);

  const needsPrice = pricetype === "LIMIT" || pricetype === "SL";
  const needsTrigger = pricetype === "SL" || pricetype === "SL-M";

  const isValid = (): boolean => {
    if (!order) return false;
    if (quantity <= 0) return false;
    if (needsPrice && price <= 0) return false;
    if (needsTrigger && triggerPrice <= 0) return false;
    return true;
  };

  const handleSubmit = async () => {
    if (!order || !isValid()) {
      toast.error("Fill quantity / price as required");
      return;
    }
    setSubmitting(true);
    try {
      const resp = await modifyOrder({
        orderid: order.orderid,
        quantity,
        pricetype,
        price: needsPrice ? price : 0,
        ...(needsTrigger ? { trigger_price: triggerPrice } : {}),
        symbol: order.symbol,
        exchange: order.exchange,
        action: order.action.toUpperCase() as "BUY" | "SELL",
        product: order.product as "MIS" | "NRML" | "CNC",
      });
      if (resp.status === "success") {
        toast.success(`Order modified: ${resp.orderid ?? order.orderid}`);
        onModified?.(order.orderid);
        onOpenChange(false);
      } else {
        toast.error(resp.message ?? "Modify failed");
      }
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { message?: string } }; message?: string })
          ?.response?.data?.message ??
        (err as { message?: string })?.message ??
        "Modify failed";
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-base">Modify order</DialogTitle>
          {order && (
            <p className="text-xs text-muted-foreground">
              <span className="font-mono font-semibold">{order.symbol}</span>
              {" · "}
              <span
                className={
                  order.action.toUpperCase() === "BUY"
                    ? "font-semibold text-emerald-600 dark:text-emerald-400"
                    : "font-semibold text-rose-600 dark:text-rose-400"
                }
              >
                {order.action}
              </span>
              {" · "}
              <span className="font-mono">{order.product}</span>
              {" · "}
              <span className="font-mono">#{order.orderid}</span>
            </p>
          )}
        </DialogHeader>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label className="text-xs">Quantity</Label>
            <Input
              type="number"
              min={1}
              step={1}
              value={quantity}
              onChange={(e) => setQuantity(parseInt(e.target.value, 10) || 0)}
              disabled={submitting}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Price type</Label>
            <select
              value={pricetype}
              onChange={(e) => setPricetype(e.target.value as PriceType)}
              disabled={submitting}
              className="h-9 w-full rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
            >
              {PRICE_TYPES.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">Price {needsPrice ? "" : "(MKT)"}</Label>
            <Input
              type="number"
              min={0}
              step={0.05}
              value={price}
              onChange={(e) => setPrice(Number(e.target.value) || 0)}
              disabled={submitting || !needsPrice}
              placeholder={needsPrice ? "0.00" : "MKT"}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">
              Trigger {needsTrigger ? "" : "(n/a)"}
            </Label>
            <Input
              type="number"
              min={0}
              step={0.05}
              value={triggerPrice}
              onChange={(e) => setTriggerPrice(Number(e.target.value) || 0)}
              disabled={submitting || !needsTrigger}
              placeholder={needsTrigger ? "0.00" : "—"}
            />
          </div>
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={submitting || !isValid()}>
            {submitting ? "Modifying…" : "Modify"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
