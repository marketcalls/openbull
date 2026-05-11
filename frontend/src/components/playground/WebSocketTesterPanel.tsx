/**
 * Top-level WebSocket tester — composes ConnectionPanel + MessageComposer +
 * MessageLog into the openalgo-style two-column layout (controls left,
 * scrolling log right).
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { ConnectionPanel } from "./ConnectionPanel";
import { MessageComposer } from "./MessageComposer";
import { MessageLog } from "./MessageLog";
import { useWebSocketTester } from "@/hooks/useWebSocketTester";

interface WebSocketTesterPanelProps {
  apiKey?: string;
  initialMessage?: string;
}

export function WebSocketTesterPanel({ apiKey, initialMessage }: WebSocketTesterPanelProps) {
  const [messageBody, setMessageBody] = useState("");

  useEffect(() => {
    if (initialMessage) setMessageBody(initialMessage);
  }, [initialMessage]);

  const {
    isConnected,
    isConnecting,
    isAuthenticated,
    wsUrl,
    error,
    connect,
    disconnect,
    sendMessage,
    messages,
    clearMessages,
    exportMessages,
    ping,
    lastLatency,
    averageLatency,
    autoReconnect,
    setAutoReconnect,
  } = useWebSocketTester();

  const handleSendMessage = (message: string) => {
    const success = sendMessage(message);
    if (success) toast.success("Message sent");
  };

  return (
    <div className="flex h-full w-full">
      {/* Left — connection + composer */}
      <div className="w-[450px] flex flex-col gap-3 p-3 border-r border-border">
        <ConnectionPanel
          isConnected={isConnected}
          isConnecting={isConnecting}
          isAuthenticated={isAuthenticated}
          wsUrl={wsUrl}
          lastLatency={lastLatency}
          averageLatency={averageLatency}
          autoReconnect={autoReconnect}
          onConnect={connect}
          onDisconnect={disconnect}
          onAutoReconnectChange={setAutoReconnect}
          onPing={ping}
        />

        {error && (
          <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-2 text-xs text-red-400">
            {error}
          </div>
        )}

        <div className="flex-1 min-h-0">
          <MessageComposer
            value={messageBody}
            onChange={setMessageBody}
            onSend={handleSendMessage}
            disabled={!isAuthenticated}
            apiKey={apiKey}
          />
        </div>
      </div>

      {/* Right — log */}
      <div className="flex-1 min-w-0">
        <MessageLog
          messages={messages}
          onClear={clearMessages}
          onExport={exportMessages}
        />
      </div>
    </div>
  );
}
