/**
 * Tiny CSV download helper used by the Orderbook / Tradebook / Positions /
 * Holdings export buttons. Single dependency-free utility — keeps everything
 * client-side so users can save what they're currently seeing without an
 * extra round-trip.
 *
 * Behaviour:
 *  - UTF-8 BOM prefix so Excel opens unicode (₹, accented names) correctly.
 *  - RFC-4180 quoting: cells with quotes, commas, or newlines are wrapped in
 *    double quotes and any inner double-quote is doubled.
 *  - Filename gets an Asia/Kolkata date-time suffix so multiple exports in
 *    the same session don't overwrite each other in the Downloads folder.
 */

export interface CsvColumn<T> {
  header: string;
  value: (row: T) => string | number | null | undefined;
}

function quoteCell(raw: string | number | null | undefined): string {
  if (raw === null || raw === undefined) return "";
  const s = String(raw);
  // Quote if the cell contains a delimiter, quote, or newline.
  if (/[",\r\n]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function istStamp(): string {
  const now = new Date();
  // Format YYYYMMDD_HHMMSS in Asia/Kolkata
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })
    .formatToParts(now)
    .reduce<Record<string, string>>((acc, p) => {
      acc[p.type] = p.value;
      return acc;
    }, {});
  return `${parts.year}${parts.month}${parts.day}_${parts.hour}${parts.minute}${parts.second}`;
}

export function buildCsv<T>(columns: CsvColumn<T>[], rows: T[]): string {
  const head = columns.map((c) => quoteCell(c.header)).join(",");
  const body = rows
    .map((r) => columns.map((c) => quoteCell(c.value(r))).join(","))
    .join("\r\n");
  return rows.length ? `${head}\r\n${body}\r\n` : `${head}\r\n`;
}

export function downloadCsv<T>({
  filename,
  columns,
  rows,
}: {
  filename: string;
  columns: CsvColumn<T>[];
  rows: T[];
}): void {
  const csv = buildCsv(columns, rows);
  // BOM forces Excel to read as UTF-8 instead of ANSI / cp1252.
  const blob = new Blob(["﻿" + csv], {
    type: "text/csv;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${filename}_${istStamp()}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Give the browser a tick to start the download before revoking.
  setTimeout(() => URL.revokeObjectURL(url), 100);
}
