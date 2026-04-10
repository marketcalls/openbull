import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "@/config/api";

interface DownloadStatus {
  status: "idle" | "downloading" | "success" | "error";
  message: string;
  broker: string | null;
  total_symbols: number;
  duration_seconds: number | null;
}

async function fetchStatus(): Promise<DownloadStatus> {
  const response = await api.get<DownloadStatus>("/web/symbols/status");
  return response.data;
}

export function MasterContractStatus() {
  const { data: status, refetch } = useQuery({
    queryKey: ["master-contract-status"],
    queryFn: fetchStatus,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "downloading" ? 2000 : false;
    },
    staleTime: 5000,
  });

  // Poll more frequently when downloading
  useEffect(() => {
    if (status?.status === "downloading") {
      const interval = setInterval(() => refetch(), 2000);
      return () => clearInterval(interval);
    }
  }, [status?.status, refetch]);

  if (!status || status.status === "idle") {
    return null;
  }

  const ledColor = {
    downloading: "bg-blue-500 animate-pulse",
    success: "bg-green-500",
    error: "bg-red-500",
    idle: "bg-gray-400",
  }[status.status];

  const tooltip = {
    downloading: status.message,
    success: `${status.total_symbols.toLocaleString()} symbols loaded${status.duration_seconds ? ` in ${status.duration_seconds}s` : ""}`,
    error: status.message,
    idle: "",
  }[status.status];

  return (
    <div className="flex items-center gap-2" title={tooltip}>
      <span className={`inline-block h-2.5 w-2.5 rounded-full ${ledColor}`} />
      <span className="hidden text-xs text-muted-foreground sm:inline">
        {status.status === "downloading" && "Downloading contracts..."}
        {status.status === "success" &&
          `${status.total_symbols.toLocaleString()} symbols`}
        {status.status === "error" && "Download failed"}
      </span>
    </div>
  );
}
