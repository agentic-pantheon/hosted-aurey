/** Distinct hues for charts (separate from brand amber `--chart-*` tokens). */

export const CHART_RAINBOW = [
  "var(--viz-1)",
  "var(--viz-2)",
  "var(--viz-3)",
  "var(--viz-4)",
  "var(--viz-5)",
  "var(--viz-6)",
  "var(--viz-7)",
  "var(--viz-8)",
  "var(--viz-9)",
  "var(--viz-10)",
] as const;

export function chartRainbowColor(index: number): string {
  return CHART_RAINBOW[((index % CHART_RAINBOW.length) + CHART_RAINBOW.length) % CHART_RAINBOW.length];
}
