import api from "@/config/api";
import type { FundsData, OrderbookItem, TradebookItem, PositionItem, HoldingItem } from "@/types/order";

export async function getDashboard(): Promise<FundsData> {
  const response = await api.get<FundsData>("/web/dashboard");
  return response.data;
}

export async function getOrderbook(): Promise<OrderbookItem[]> {
  const response = await api.get<OrderbookItem[]>("/web/orderbook");
  return response.data;
}

export async function getTradebook(): Promise<TradebookItem[]> {
  const response = await api.get<TradebookItem[]>("/web/tradebook");
  return response.data;
}

export async function getPositions(): Promise<PositionItem[]> {
  const response = await api.get<PositionItem[]>("/web/positions");
  return response.data;
}

export async function getHoldings(): Promise<HoldingItem[]> {
  const response = await api.get<HoldingItem[]>("/web/holdings");
  return response.data;
}
