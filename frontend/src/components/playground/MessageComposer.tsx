/**
 * Composer panel for WebSocket messages — template gallery + JSON editor +
 * Send (Ctrl+Enter).
 *
 * Templates are adapted from openalgo to use openbull's WS protocol:
 *  - "symbols" array (not a single "symbol"), even for one-symbol subscribe
 *  - string mode ("LTP" | "QUOTE" | "DEPTH" | "FULL"), not numeric 1/2/3
 *  - "api_key" (with underscore) on the authenticate message
 *
 * Example symbols follow the agreed convention: NIFTY/BANKNIFTY for indices
 * (NSE_INDEX), RELIANCE/NHPC/TCS/INFY/SBIN/AXISBANK for cash equities (NSE).
 */
import { useCallback, useEffect, useState } from "react";
import { FileText, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import { JsonEditor } from "@/components/ui/json-editor";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { MessageTemplate } from "@/types/websocket";

interface CategorizedTemplate extends MessageTemplate {
  category: "auth" | "subscribe" | "depth" | "unsubscribe";
}

function getMessageTemplates(): CategorizedTemplate[] {
  return [
    // Authentication
    {
      key: "authenticate",
      label: "Authenticate",
      description: "Authenticate with the WS proxy",
      template: { action: "authenticate", api_key: "{{API_KEY}}" },
      category: "auth",
    },

    // Subscribe — single symbol
    {
      key: "subscribe_ltp_index",
      label: "Subscribe LTP — Index",
      description: "NIFTY on NSE_INDEX",
      template: {
        action: "subscribe",
        symbols: [{ symbol: "NIFTY", exchange: "NSE_INDEX" }],
        mode: "LTP",
      },
      category: "subscribe",
    },
    {
      key: "subscribe_ltp_equity",
      label: "Subscribe LTP — Equity",
      description: "RELIANCE on NSE",
      template: {
        action: "subscribe",
        symbols: [{ symbol: "RELIANCE", exchange: "NSE" }],
        mode: "LTP",
      },
      category: "subscribe",
    },
    {
      key: "subscribe_quote_index",
      label: "Subscribe Quote — Index",
      description: "BANKNIFTY OHLC + OI",
      template: {
        action: "subscribe",
        symbols: [{ symbol: "BANKNIFTY", exchange: "NSE_INDEX" }],
        mode: "QUOTE",
      },
      category: "subscribe",
    },
    {
      key: "subscribe_quote_option",
      label: "Subscribe Quote — Option",
      description: "NIFTY ATM CE on NFO",
      template: {
        action: "subscribe",
        symbols: [{ symbol: "NIFTY12MAY2624250CE", exchange: "NFO" }],
        mode: "QUOTE",
      },
      category: "subscribe",
    },
    {
      key: "subscribe_multiple",
      label: "Subscribe Multiple",
      description: "RELIANCE + TCS + INFY",
      template: {
        action: "subscribe",
        symbols: [
          { symbol: "RELIANCE", exchange: "NSE" },
          { symbol: "TCS", exchange: "NSE" },
          { symbol: "INFY", exchange: "NSE" },
        ],
        mode: "LTP",
      },
      category: "subscribe",
    },

    // Depth
    {
      key: "subscribe_depth_equity",
      label: "Subscribe Depth — Equity",
      description: "SBIN 5-level order book",
      template: {
        action: "subscribe",
        symbols: [{ symbol: "SBIN", exchange: "NSE" }],
        mode: "DEPTH",
      },
      category: "depth",
    },
    {
      key: "subscribe_depth_option",
      label: "Subscribe Depth — Option",
      description: "NIFTY ATM CE 5-level depth",
      template: {
        action: "subscribe",
        symbols: [{ symbol: "NIFTY12MAY2624250CE", exchange: "NFO" }],
        mode: "DEPTH",
      },
      category: "depth",
    },

    // Unsubscribe
    {
      key: "unsubscribe_ltp",
      label: "Unsubscribe LTP",
      description: "Stop streaming LTP",
      template: {
        action: "unsubscribe",
        symbols: [{ symbol: "RELIANCE", exchange: "NSE" }],
        mode: "LTP",
      },
      category: "unsubscribe",
    },
    {
      key: "unsubscribe_quote",
      label: "Unsubscribe Quote",
      description: "Stop streaming Quote",
      template: {
        action: "unsubscribe",
        symbols: [{ symbol: "BANKNIFTY", exchange: "NSE_INDEX" }],
        mode: "QUOTE",
      },
      category: "unsubscribe",
    },
    {
      key: "unsubscribe_depth",
      label: "Unsubscribe Depth",
      description: "Stop streaming Depth",
      template: {
        action: "unsubscribe",
        symbols: [{ symbol: "SBIN", exchange: "NSE" }],
        mode: "DEPTH",
      },
      category: "unsubscribe",
    },
  ];
}

const CATEGORY_LABELS: Record<CategorizedTemplate["category"], string> = {
  auth: "Authentication",
  subscribe: "Subscriptions",
  depth: "Market Depth",
  unsubscribe: "Unsubscribe",
};

function getTemplatesByCategory(): Record<string, CategorizedTemplate[]> {
  const grouped: Record<string, CategorizedTemplate[]> = {};
  for (const t of getMessageTemplates()) {
    if (!grouped[t.category]) grouped[t.category] = [];
    grouped[t.category].push(t);
  }
  return grouped;
}

interface MessageComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSend: (message: string) => void;
  disabled?: boolean;
  apiKey?: string;
}

export function MessageComposer({
  value,
  onChange,
  onSend,
  disabled = false,
  apiKey = "",
}: MessageComposerProps) {
  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null);

  const applyTemplate = useCallback(
    (templateKey: string) => {
      const template = getMessageTemplates().find((t) => t.key === templateKey);
      if (!template) return;

      let str = JSON.stringify(template.template, null, 2);
      if (apiKey && str.includes("{{API_KEY}}")) {
        str = str.replace("{{API_KEY}}", apiKey);
      }
      onChange(str);
      setSelectedTemplate(templateKey);
    },
    [onChange, apiKey],
  );

  const handleSend = useCallback(() => {
    if (!value.trim()) return;
    onSend(value);
  }, [value, onSend]);

  // Ctrl/Cmd + Enter sends.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && !disabled) {
        e.preventDefault();
        handleSend();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handleSend, disabled]);

  return (
    <div className="flex flex-col h-full bg-card/50 rounded-lg border border-border overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-border bg-card/30">
        <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
        <Select
          value={selectedTemplate || ""}
          onValueChange={(v) => {
            if (v) applyTemplate(v);
          }}
          disabled={disabled}
        >
          <SelectTrigger className="flex-1 h-8 text-xs">
            <SelectValue placeholder="Select a template..." />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(getTemplatesByCategory()).map(([category, templates]) => (
              <SelectGroup key={category}>
                <SelectLabel className="text-xs font-semibold text-muted-foreground">
                  {CATEGORY_LABELS[category as CategorizedTemplate["category"]]}
                </SelectLabel>
                {templates.map((t) => (
                  <SelectItem key={t.key} value={t.key}>
                    {t.label} — {t.description}
                  </SelectItem>
                ))}
              </SelectGroup>
            ))}
          </SelectContent>
        </Select>
        {selectedTemplate && (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => {
              setSelectedTemplate(null);
              onChange("");
            }}
          >
            Clear
          </Button>
        )}
      </div>

      <div className="flex-1 min-h-0">
        <JsonEditor
          value={value}
          onChange={onChange}
          placeholder='{"action": "authenticate", "api_key": "..."}'
          className="h-full"
          readOnly={disabled}
        />
      </div>

      <div className="flex items-center justify-between px-3 py-2 border-t border-border bg-card/30">
        <span className="text-[10px] text-muted-foreground">Ctrl+Enter to send</span>
        <Button
          size="sm"
          className={cn(
            "h-8 px-4",
            disabled
              ? "bg-muted text-muted-foreground cursor-not-allowed"
              : "bg-sky-600 hover:bg-sky-700 text-white",
          )}
          onClick={handleSend}
          disabled={disabled || !value.trim()}
        >
          <Send className="h-3.5 w-3.5 mr-1.5" />
          Send Message
        </Button>
      </div>
    </div>
  );
}
