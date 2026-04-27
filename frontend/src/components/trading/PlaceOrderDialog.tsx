import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { placeOrder } from "@/api/optionchain";
import { cn } from "@/lib/utils";

const PRICE_TYPES = [
  { value: "MARKET", label: "Market" },
  { value: "LIMIT", label: "Limit" },
  { value: "SL-M", label: "SL-M" },
  { value: "SL", label: "SL-L" },
] as const;

const FNO_PRODUCTS = [
  { value: "NRML", label: "NRML" },
  { value: "MIS", label: "MIS" },
] as const;

const EQUITY_PRODUCTS = [
  { value: "CNC", label: "CNC" },
  { value: "MIS", label: "MIS" },
] as const;

type PriceType = (typeof PRICE_TYPES)[number]["value"];
type Product = "MIS" | "NRML" | "CNC";

export interface PlaceOrderDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  symbol: string;
  exchange: string;
  action: "BUY" | "SELL";
  ltp?: number;
  lotSize: number;
  tickSize: number;
  strategy?: string;
  onSuccess?: (orderid: string) => void;
}

function isFnOExchange(exchange: string): boolean {
  return ["NFO", "BFO", "MCX", "CDS", "BCD", "NCDEX"].includes(exchange);
}

function roundToTick(price: number, tickSize: number): number {
  if (tickSize <= 0) return price;
  return Number((Math.round(price / tickSize) * tickSize).toFixed(2));
}

function adjustPrice(price: number, tickSize: number, dir: "up" | "down"): number {
  const rounded = roundToTick(price, tickSize);
  if (dir === "up") return Number((rounded + tickSize).toFixed(2));
  return Math.max(0, Number((rounded - tickSize).toFixed(2)));
}

export function PlaceOrderDialog({
  open,
  onOpenChange,
  symbol,
  exchange,
  action,
  ltp,
  lotSize,
  tickSize,
  strategy = "OptionChain",
  onSuccess,
}: PlaceOrderDialogProps) {
  const [formAction, setFormAction] = useState<"BUY" | "SELL">(action);
  const [quantityMode, setQuantityMode] = useState<"lots" | "shares">("lots");
  const [lots, setLots] = useState(1);
  const [priceType, setPriceType] = useState<PriceType>("MARKET");
  const [product, setProduct] = useState<Product>(
    isFnOExchange(exchange) ? "NRML" : "CNC"
  );
  const [price, setPrice] = useState(0);
  const [triggerPrice, setTriggerPrice] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  const productOptions = isFnOExchange(exchange) ? FNO_PRODUCTS : EQUITY_PRODUCTS;
  const totalQty = lots * lotSize;
  const needsPrice = priceType === "LIMIT" || priceType === "SL";
  const needsTrigger = priceType === "SL" || priceType === "SL-M";

  useEffect(() => {
    if (!open) return;
    setFormAction(action);
    setQuantityMode("lots");
    setLots(1);
    setPriceType("MARKET");
    setProduct(isFnOExchange(exchange) ? "NRML" : "CNC");
    setPrice(ltp ? roundToTick(ltp, tickSize) : 0);
    setTriggerPrice(0);
  }, [open, action, exchange, ltp, tickSize]);

  const isValid = (): boolean => {
    if (!symbol || !exchange) return false;
    if (totalQty <= 0) return false;
    if (needsPrice && price <= 0) return false;
    if (needsTrigger && triggerPrice <= 0) return false;
    return true;
  };

  const handleQuantityChange = (raw: string) => {
    const n = parseInt(raw, 10) || 0;
    if (quantityMode === "lots") {
      setLots(Math.max(1, n));
    } else {
      const rounded = Math.max(lotSize, Math.round(n / lotSize) * lotSize);
      setLots(rounded / lotSize);
    }
  };

  const handleSubmit = async () => {
    if (!isValid()) {
      toast.error("Please fill all required fields");
      return;
    }
    setSubmitting(true);
    try {
      const resp = await placeOrder({
        strategy,
        symbol,
        exchange,
        action: formAction,
        quantity: totalQty,
        pricetype: priceType,
        product,
        ...(needsPrice ? { price } : {}),
        ...(needsTrigger ? { trigger_price: triggerPrice } : {}),
      });
      if (resp.status === "success" && resp.orderid) {
        toast.success(`Order placed: ${resp.orderid}`);
        onSuccess?.(resp.orderid);
        onOpenChange(false);
      } else {
        toast.error(resp.message ?? "Order placement failed");
      }
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { message?: string } }; message?: string })
          ?.response?.data?.message ??
        (err as { message?: string })?.message ??
        "Order placement failed";
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const displayQty = quantityMode === "lots" ? lots : totalQty;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-sm">
            <span>Place Order —</span>
            <span
              className={
                formAction === "BUY"
                  ? "text-green-600 dark:text-green-400 font-semibold"
                  : "text-red-600 dark:text-red-400 font-semibold"
              }
            >
              {formAction}
            </span>
            <span className="text-muted-foreground font-normal truncate">{symbol}</span>
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {ltp !== undefined && (
            <div className="rounded-md bg-muted/50 px-3 py-2 text-xs flex justify-between">
              <span className="text-muted-foreground">LTP</span>
              <span className="font-mono font-semibold">{ltp.toFixed(2)}</span>
            </div>
          )}

          {/* Action toggle */}
          <div className="space-y-1.5">
            <Label className="text-xs">Action</Label>
            <div className="flex gap-2">
              <Button
                type="button"
                variant={formAction === "BUY" ? "default" : "outline"}
                className={cn(
                  "flex-1",
                  formAction === "BUY" && "bg-green-600 text-white hover:bg-green-700"
                )}
                onClick={() => setFormAction("BUY")}
              >
                BUY
              </Button>
              <Button
                type="button"
                variant={formAction === "SELL" ? "default" : "outline"}
                className={cn(
                  "flex-1",
                  formAction === "SELL" && "bg-red-600 text-white hover:bg-red-700"
                )}
                onClick={() => setFormAction("SELL")}
              >
                SELL
              </Button>
            </div>
          </div>

          {/* Quantity */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label className="text-xs">Quantity</Label>
              <div className="flex gap-1">
                <button
                  type="button"
                  onClick={() => setQuantityMode("lots")}
                  className={cn(
                    "px-2 py-0.5 text-[10px] rounded",
                    quantityMode === "lots"
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground hover:bg-muted/80"
                  )}
                >
                  Lots
                </button>
                <button
                  type="button"
                  onClick={() => setQuantityMode("shares")}
                  className={cn(
                    "px-2 py-0.5 text-[10px] rounded",
                    quantityMode === "shares"
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground hover:bg-muted/80"
                  )}
                >
                  Shares
                </button>
              </div>
            </div>
            <Input
              type="number"
              value={displayQty}
              onChange={(e) => handleQuantityChange(e.target.value)}
              min={1}
            />
            <div className="flex justify-between text-[10px] text-muted-foreground">
              <span>Lot size: {lotSize}</span>
              <span>Total qty: {totalQty}</span>
            </div>
          </div>

          {/* Price type + product */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Price Type</Label>
              <select
                value={priceType}
                onChange={(e) => setPriceType(e.target.value as PriceType)}
                className="h-8 w-full rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {PRICE_TYPES.map((pt) => (
                  <option key={pt.value} value={pt.value}>
                    {pt.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Product</Label>
              <select
                value={product}
                onChange={(e) => setProduct(e.target.value as Product)}
                className="h-8 w-full rounded-lg border border-input bg-background px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {productOptions.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {needsPrice && (
            <div className="space-y-1.5">
              <Label className="text-xs">Price</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="px-2"
                  onClick={() => setPrice(adjustPrice(price, tickSize, "down"))}
                >
                  −
                </Button>
                <Input
                  type="number"
                  value={price}
                  onChange={(e) => setPrice(parseFloat(e.target.value) || 0)}
                  onBlur={() => setPrice(roundToTick(price, tickSize))}
                  className="flex-1 text-center"
                  step={tickSize}
                  min={0}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="px-2"
                  onClick={() => setPrice(adjustPrice(price, tickSize, "up"))}
                >
                  +
                </Button>
              </div>
              <p className="text-[10px] text-muted-foreground">Tick size: {tickSize}</p>
            </div>
          )}

          {needsTrigger && (
            <div className="space-y-1.5">
              <Label className="text-xs">Trigger Price</Label>
              <div className="flex gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="px-2"
                  onClick={() => setTriggerPrice(adjustPrice(triggerPrice, tickSize, "down"))}
                >
                  −
                </Button>
                <Input
                  type="number"
                  value={triggerPrice}
                  onChange={(e) => setTriggerPrice(parseFloat(e.target.value) || 0)}
                  onBlur={() => setTriggerPrice(roundToTick(triggerPrice, tickSize))}
                  className="flex-1 text-center"
                  step={tickSize}
                  min={0}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="px-2"
                  onClick={() => setTriggerPrice(adjustPrice(triggerPrice, tickSize, "up"))}
                >
                  +
                </Button>
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!isValid() || submitting}
            className={cn(
              formAction === "BUY"
                ? "bg-green-600 text-white hover:bg-green-700"
                : "bg-red-600 text-white hover:bg-red-700"
            )}
          >
            {submitting ? "Placing…" : `Place ${formAction} Order`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
