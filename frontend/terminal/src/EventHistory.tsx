import { memo, useMemo, useState } from "react";
import { EventDetails } from "./backtest/BacktestChart";
import { type StrategyEvent } from "./backtest/types";

const pageSize = 50;
const EventRow = memo(function EventRow({ event, onLocate }: {
  event: StrategyEvent; onLocate: (event: StrategyEvent) => void;
}) {
  return <button data-event-id={event.id} onClick={() => onLocate(event)}>
    <EventDetails event={event} /><small className="event-record-id">{event.id}</small>
  </button>;
});

export const EventList = memo(function EventList({ events, onLocate, label }: {
  events: StrategyEvent[]; onLocate: (event: StrategyEvent) => void; label: string;
}) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const matches = useMemo(() => {
    const search = query.trim().toLowerCase();
    return search ? events.filter(e => [e.id, e.order_id, e.group_id].some(value =>
      value?.toLowerCase().includes(search))) : events;
  }, [events, query]);
  const pages = Math.max(1, Math.ceil(matches.length / pageSize));
  const current = Math.min(page, pages - 1);
  return <div className="event-records" role="region" aria-label={label}>
    <div className="event-navigation">
      <label>按事件或订单 ID 检索 <input value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} /></label>
      <span>共 {events.length} 条，匹配 {matches.length} 条</span>
      <button disabled={current === 0} onClick={() => setPage(current - 1)}>上一页</button>
      <label>事件页码 <input type="number" min={1} max={pages} value={current + 1}
        onChange={e => setPage(Math.max(0, Math.min(pages - 1, Number(e.target.value) - 1)))} /></label>
      <span>/ {pages} 页</span>
      <button disabled={current + 1 >= pages} onClick={() => setPage(current + 1)}>下一页</button>
    </div>
    <div className="event-list">
      {matches.slice(current * pageSize, (current + 1) * pageSize).map(e =>
        <EventRow key={e.id} event={e} onLocate={onLocate} />)}
      {!matches.length && <p>没有匹配的事件记录。</p>}
    </div>
  </div>;
});

export const EventHistory = memo(function EventHistory({ events, onLocate }: {
  events: StrategyEvent[]; onLocate: (event: StrategyEvent) => void;
}) {
  const [open, setOpen] = useState(false);
  return <details onToggle={e => setOpen(e.currentTarget.open)}>
    <summary><span>所选市场日信号与交易记录</span> · {events.length} 条</summary>
    {open && <EventList events={events} onLocate={onLocate} label="历史信号与交易记录" />}
  </details>;
});
