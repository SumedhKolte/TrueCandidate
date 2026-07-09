/**
 * Cinematic dark-navy theme. All color decisions were validated with the
 * dataviz palette validator against surface #0e1626 (see repo README):
 *   categorical series (fixed order, never cycled):
 *     blue #3987e5 · yellow #c98500 · aqua #199e70 · violet #9085e9 · red #e66767
 *   status: good #0ca30c / critical #d03b3b (badges always carry +/- icon + text)
 */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        page: "#070d1a",       // page plane
        surface: "#0e1626",    // card / chart surface (dark navy)
        edge: "rgba(255,255,255,0.08)",
        ink: "#f4f6fb",        // primary text
        "ink-2": "#b9c2d4",    // secondary text
        muted: "#7c8699",      // axis / labels
        grid: "#1d2940",       // hairline gridlines
        accent: "#3987e5",     // series-1 blue, also the gauge hue
        good: "#0ca30c",
        bad: "#d03b3b",
      },
      fontFamily: {
        sans: ["system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      },
    },
  },
  plugins: [],
};
