import api from "@/config/api";
import type { SymbolSearchResult } from "@/types/symbol";

export async function searchSymbols(
  q: string,
  exchange: string
): Promise<SymbolSearchResult[]> {
  const response = await api.get<{ status: string; data: SymbolSearchResult[] }>(
    "/web/symbols/search",
    { params: { q, exchange } }
  );
  return response.data.data;
}
