import { motion } from "framer-motion";
import AnimatedNumber from "./AnimatedNumber";

/**
 * Semicircular confidence gauge. The arc springs to each new score, a soft
 * glow in the leader's hue breathes behind it, and the number rolls rather
 * than snaps. Value text wears ink (never the series color) — the arc alone
 * carries identity.
 */
export default function ConfidenceGauge({ score, name, color = "#3987e5" }) {
  const R = 84;
  const CX = 100;
  const CY = 100;
  const circumference = Math.PI * R;
  const filled = (score / 100) * circumference;

  return (
    <div className="relative flex flex-col items-center">
      {/* breathing glow behind the gauge */}
      <motion.div
        className="pointer-events-none absolute top-2 h-36 w-36 rounded-full"
        style={{ background: color, filter: "blur(70px)" }}
        animate={{ opacity: [0.10, 0.22, 0.10] }}
        transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
        aria-hidden
      />
      <svg viewBox="0 0 200 112" className="relative w-full max-w-[260px]">
        {/* track */}
        <path
          d={`M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}`}
          fill="none" stroke="#1d2940" strokeWidth="10" strokeLinecap="round"
        />
        {/* value arc — springs to each new score */}
        <motion.path
          d={`M ${CX - R} ${CY} A ${R} ${R} 0 0 1 ${CX + R} ${CY}`}
          fill="none" stroke={color} strokeWidth="10" strokeLinecap="round"
          animate={{ strokeDasharray: `${filled} ${circumference}` }}
          transition={{ type: "spring", stiffness: 60, damping: 16 }}
          style={{ filter: `drop-shadow(0 0 6px ${color}66)` }}
        />
        <foreignObject x={CX - 40} y={CY - 46} width="80" height="40">
          <div className="flex h-full items-end justify-center">
            <AnimatedNumber
              value={score}
              className="text-[34px] font-semibold leading-none text-ink"
            />
          </div>
        </foreignObject>
        <text x={CX} y={CY + 6} textAnchor="middle"
              className="fill-muted" fontSize="10" letterSpacing="2">
          CONFIDENCE
        </text>
      </svg>
      <motion.div
        key={name}
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        className="mt-1 text-sm text-ink-2"
      >
        Best match: <span className="font-medium text-ink">{name ?? "—"}</span>
      </motion.div>
    </div>
  );
}
