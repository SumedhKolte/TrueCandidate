import { createClient } from "@supabase/supabase-js";

// Anon key only: the dashboard reads; all writes go backend -> service key.
export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,
  import.meta.env.VITE_SUPABASE_ANON_KEY,
);

/** Fixed categorical order (validated against surface #0e1626, worst adjacent
 *  CVD ΔE 41.3). Color follows the ENTITY: assigned once by join order and
 *  never reshuffled when the roster or ranking changes. */
export const SERIES = ["#3987e5", "#c98500", "#199e70", "#9085e9", "#e66767"];

export const colorFor = (participants, id) => {
  const idx = [...participants]
    .sort((a, b) => new Date(a.joined_at) - new Date(b.joined_at))
    .findIndex((p) => p.id === id);
  return SERIES[idx % SERIES.length];
};
