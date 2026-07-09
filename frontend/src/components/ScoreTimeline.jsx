import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { colorFor } from "../lib/supabase";

const fmtTime = (t) =>
  new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function ChartTooltip({ active, payload, label, byId }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-edge bg-page/95 p-3 text-xs shadow-xl">
      <div className="mb-1 text-muted tabular-nums">{fmtTime(label)}</div>
      {payload.map((entry) => (
        <div key={entry.dataKey} className="flex items-center gap-2 py-0.5">
          <span className="h-2 w-2 rounded-full" style={{ background: entry.color }} />
          <span className="text-ink-2">{byId[entry.dataKey]?.display_name}</span>
          <span className="ml-auto pl-4 font-semibold text-ink tabular-nums">
            {entry.value}
          </span>
        </div>
      ))}
    </div>
  );
}

/**
 * Live score timeline — every kink in a line is one signal from the ledger.
 * Chart rules applied: single 0-100 axis, thin 2px lines, recessive hairline
 * grid, legend + direct end-labels (identity never rides on color alone),
 * crosshair tooltip.
 */
export default function ScoreTimeline({ timeline, participants }) {
  const contenders = participants.filter((p) => !p.is_interviewer);
  const byId = Object.fromEntries(contenders.map((p) => [p.id, p]));
  const last = timeline.length - 1;

  const endLabel = (pid) => (props) =>
    props.index === last ? (
      <text x={props.x + 8} y={props.y + 4} fontSize="11"
            fill={colorFor(participants, pid)}>
        {byId[pid]?.display_name?.split(" ")[0]}
      </text>
    ) : null;

  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={timeline} margin={{ top: 8, right: 70, bottom: 0, left: -16 }}>
        <CartesianGrid stroke="#1d2940" strokeWidth={1} vertical={false} />
        <XAxis
          dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]}
          tickFormatter={fmtTime} stroke="#1d2940" tickLine={false}
        />
        <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]}
               stroke="#1d2940" tickLine={false} />
        <Tooltip
          content={<ChartTooltip byId={byId} />}
          cursor={{ stroke: "#7c8699", strokeDasharray: "3 3" }}
        />
        <Legend
          formatter={(id) => (
            <span className="text-xs text-ink-2">{byId[id]?.display_name ?? id}</span>
          )}
        />
        {contenders.map((p) => (
          <Line
            key={p.id}
            dataKey={p.id}
            stroke={colorFor(participants, p.id)}
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
            label={endLabel(p.id)}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
