import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { checkinToday, getStudyCalendar } from "../api/learning";
import { Icon } from "./Icon";

const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
const dateKey = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const initialMonth = () => new Date(new Date().getFullYear(), new Date().getMonth(), 1);

export function StudyCalendar() {
  const currentMonth = dateKey(initialMonth()).slice(0, 7);
  const [month, setMonth] = useState(initialMonth);
  const [expanded, setExpanded] = useState(false);
  const key = dateKey(month).slice(0, 7);
  const cache = useQueryClient();
  const today = useQuery({ queryKey: ["study-calendar", currentMonth, timezone], queryFn: () => getStudyCalendar(currentMonth, timezone), refetchInterval: 60_000 });
  const selected = useQuery({ queryKey: ["study-calendar", key, timezone], queryFn: () => getStudyCalendar(key, timezone), enabled: expanded && key !== currentMonth });
  const calendar = key === currentMonth ? today : selected;
  const checkin = useMutation({ mutationFn: () => checkinToday(timezone), onSuccess: () => { void cache.invalidateQueries({ queryKey: ["study-calendar"] }); } });
  const checked = new Set(calendar.data?.dates ?? []);
  const offset = (month.getDay() + 6) % 7;
  const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const changeMonth = (step: number) => setMonth((current) => new Date(current.getFullYear(), current.getMonth() + step, 1));
  const formatter = new Intl.DateTimeFormat("zh-CN", { month: "long", year: "numeric" });

  return <section className="study-checkin" aria-labelledby="checkin-title">
    <div className="checkin-heading">
      <h2 id="checkin-title">学习打卡</h2>
      <button className="checkin-action" disabled={!today.data || today.data.checked_today || checkin.isPending} onClick={() => checkin.mutate()} type="button">
        {checkin.isPending ? "打卡中…" : today.data?.checked_today ? "今日已打卡" : "今日打卡"}
      </button>
    </div>
    {today.isPending ? <p className="checkin-meta" role="status">正在读取打卡…</p> : null}
    {today.data ? <p className="checkin-meta">连续 <strong>{today.data.streak}</strong> 天 <span aria-hidden="true">·</span> 累计 {today.data.total} 天</p> : null}
    {today.error ? <p role="alert">打卡记录暂时不可用。<button className="text-action" onClick={() => void today.refetch()} type="button">重试</button></p> : null}
    {checkin.error ? <p role="alert">打卡未保存，请重试。</p> : null}
    {checkin.isSuccess ? <span className="visually-hidden" role="status">今日打卡已保存</span> : null}
    <details className="checkin-details" onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary>{expanded ? "收起月历" : "查看月历"}</summary>
      <div className="calendar-month"><button className="icon-button" aria-label="上个月" onClick={() => changeMonth(-1)} type="button"><Icon name="chevronLeft" /></button><span aria-live="polite">{formatter.format(month)}</span><button className="icon-button" aria-label="下个月" onClick={() => changeMonth(1)} type="button"><Icon name="chevronRight" /></button></div>
      {calendar.isPending ? <p role="status">正在读取月历…</p> : null}
      {calendar.error ? <p role="alert">月历暂时不可用。<button className="text-action" onClick={() => void calendar.refetch()} type="button">重试</button></p> : null}
      <div className="calendar-grid" role="list" aria-label="本月打卡记录">
        {["一", "二", "三", "四", "五", "六", "日"].map((day) => <span className="calendar-weekday" key={day} aria-hidden="true">{day}</span>)}
        {Array.from({ length: offset }, (_, i) => <span key={`blank-${i}`} aria-hidden="true" />)}
        {Array.from({ length: days }, (_, i) => {
          const day = dateKey(new Date(month.getFullYear(), month.getMonth(), i + 1));
          return <span key={day} role="listitem" aria-label={`${day}${!calendar.data ? " 正在读取" : checked.has(day) ? " 已打卡" : " 未打卡"}`} aria-current={day === today.data?.today ? "date" : undefined} className={`calendar-day${checked.has(day) ? " calendar-day--checked" : ""}`}>{i + 1}{checked.has(day) ? <span aria-hidden="true" className="calendar-mark" /> : null}</span>;
        })}
      </div>
    </details>
  </section>;
}
