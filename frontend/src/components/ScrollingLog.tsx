import React from "react";
import { ListFilter } from "lucide-react";
import type { MarketEventData } from "../hooks/useWebSocket";

interface ScrollingLogProps {
  events: MarketEventData[];
}

export const ScrollingLog: React.FC<ScrollingLogProps> = ({ events }) => {
  return (
    <div className="bg-slate-900/60 backdrop-blur-md border border-slate-800 rounded-xl p-6 shadow-xl flex-1 flex flex-col min-h-[400px]">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <ListFilter className="w-5 h-5 text-cyan-400" />
          <h3 className="text-sm font-semibold text-slate-350 uppercase tracking-wider">Silver Layer Live Market Event Stream</h3>
        </div>
        <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 font-mono">
          Last {events.length} Ticks
        </span>
      </div>

      <div className="overflow-x-auto flex-1">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-slate-800 text-[10px] uppercase font-bold text-slate-500 tracking-wider">
              <th className="py-2.5 px-3">Symbol</th>
              <th className="py-2.5 px-3">LTP (Last Price)</th>
              <th className="py-2.5 px-3 text-right">OHLC (Range)</th>
              <th className="py-2.5 px-3 text-right">Volume</th>
              <th className="py-2.5 px-3 text-center">Exchange Time</th>
              <th className="py-2.5 px-3 text-right">Correlation ID (Packet)</th>
              <th className="py-2.5 px-3 text-right">Event ID</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-850 text-xs font-mono">
            {events.length === 0 ? (
              <tr>
                <td colSpan={7} className="text-center py-12 text-slate-500 font-sans">
                  No market ticks running. Click "Start" on the replay controller to feed logs.
                </td>
              </tr>
            ) : (
              events.map((event) => (
                <tr key={event.event_id} className="hover:bg-slate-800/30 transition-colors">
                  <td className="py-2 px-3">
                    <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-cyan-950/40 text-cyan-400 border border-cyan-900/30">
                      {event.symbol}
                    </span>
                  </td>
                  <td className="py-2 px-3 font-semibold text-slate-200">
                    {event.ltp.toLocaleString("en-US", { minimumFractionDigits: 2 })}
                  </td>
                  <td className="py-2 px-3 text-right text-slate-400 text-[11px]">
                    <span className="text-slate-500">O:</span>{event.open} <span className="text-emerald-500">H:</span>{event.high} <span className="text-rose-500">L:</span>{event.low} <span className="text-slate-500">C:</span>{event.close}
                  </td>
                  <td className="py-2 px-3 text-right text-slate-350">
                    {event.volume.toLocaleString()}
                  </td>
                  <td className="py-2 px-3 text-center text-slate-400 text-[11px]">
                    {event.exchange_timestamp ? event.exchange_timestamp.substring(11, 23) : "N/A"}
                  </td>
                  <td className="py-2 px-3 text-right text-slate-500 text-[11px]" title={event.correlation_id}>
                    {event.correlation_id.substring(0, 8)}...
                  </td>
                  <td className="py-2 px-3 text-right text-slate-500 text-[11px]" title={event.event_id}>
                    {event.event_id.substring(0, 8)}...
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
