import { useEffect } from "react";
import { motion, useMotionValue, useSpring, useTransform } from "framer-motion";

/** Spring-animated integer — numbers roll to their new value instead of
 *  snapping, which is most of what makes the dashboard feel alive. */
export default function AnimatedNumber({ value, className = "" }) {
  const raw = useMotionValue(value);
  const spring = useSpring(raw, { stiffness: 80, damping: 18 });
  const display = useTransform(spring, (v) => Math.round(v));

  useEffect(() => {
    raw.set(value);
  }, [value, raw]);

  return <motion.span className={className}>{display}</motion.span>;
}
