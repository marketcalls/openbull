import api from "@/config/api";
import type { FundsData, OrderbookItem, TradebookItem, PositionItem, HoldingItem } from "@/types/order";

export async function getDashboard(): Promise<FundsData> {
  const response = await api.get<{ status: string; data: FundsData }>("/web/dashboard");
  return response.data.data;
}

export async function getOrderbook(): Promise<OrderbookItem[]> {
  const response = await api.get<{ status: string; data: { orders: OrderbookItem[] } }>("/web/orderbook");
  return response.data.data.orders;
}

export async function getTradebook(): Promise<TradebookItem[]> {
  const response = await api.get<{ status: string; data: TradebookItem[] }>("/web/tradebook");
  return response.data.data;
}

export async function getPositions(): Promise<PositionItem[]> {
  const response = await api.get<{ status: string; data: PositionItem[] }>("/web/positions");
  return response.data.data;
}

export async function getHoldings(): Promise<HoldingItem[]> {
  const response = await api.get<{ status: string; data: { holdings: HoldingItem[] } }>("/web/holdings");
  return response.data.data.holdings;
}
