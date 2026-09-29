"use client";

import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { DailyBar } from "@/lib/api/endpoints";
import type { PriceBasis } from "@/stores/preferences";

const UP = "#4f8a5b";
const DOWN = "#c9694a";

function toTime(date: string): UTCTimestamp {
  return (Date.parse(`${date}T00:00:00Z`) / 1000) as UTCTimestamp;
}

/** Candles from the provider's own values: raw as reported, or the provider's adjustment. */
export function toCandles(bars: DailyBar[], basis: PriceBasis) {
  return bars.map((bar) => {
    const adjusted = basis === "adjusted" && bar.adj_open !== null && bar.adj_high !== null
      && bar.adj_low !== null && bar.adj_close !== null;
    return {
      time: toTime(bar.date),
      open: adjusted ? bar.adj_open! : bar.open,
      high: adjusted ? bar.adj_high! : bar.high,
      low: adjusted ? bar.adj_low! : bar.low,
      close: adjusted ? bar.adj_close! : bar.close,
    };
  });
}

export function PriceChart({ bars, basis }: { bars: DailyBar[]; basis: PriceBasis }) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const instance = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#6f7c73",
        fontFamily: "var(--font-geist-sans), sans-serif",
        fontSize: 11,
      },
      grid: { vertLines: { color: "#f0f3ef" }, horzLines: { color: "#f0f3ef" } },
      rightPriceScale: { borderColor: "#e3e8e3" },
      timeScale: { borderColor: "#e3e8e3" },
    });
    candles.current = instance.addSeries(CandlestickSeries, {
      upColor: UP, downColor: DOWN, borderVisible: false, wickUpColor: UP, wickDownColor: DOWN,
    });
    volume.current = instance.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" }, priceScaleId: "volume", color: "#dfe6dc",
    });
    instance.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    chart.current = instance;
    return () => {
      instance.remove();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    candles.current?.setData(toCandles(bars, basis));
    volume.current?.setData(bars.map((bar) => ({
      time: toTime(bar.date),
      value: bar.volume,
      color: bar.close >= bar.open ? "#d5e3d2" : "#f0d9cf",
    })));
    chart.current?.timeScale().fitContent();
  }, [bars, basis]);

  return <div className="price-chart" ref={container} role="img" aria-label="Daily price chart" />;
}
