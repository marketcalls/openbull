// Plot component that pairs the smaller plotly.js-cartesian-dist-min bundle
// (~200 KB gz; bar/scatter/line traces only — no candlestick/maps/3d) with the
// react wrapper from react-plotly.js. Centralised here so other tools can
// import the same Plot component instead of each importing the heavy default.

// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — react-plotly.js/factory has no bundled types
import createPlotlyComponent from "react-plotly.js/factory";
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — plotly.js-cartesian-dist-min has no bundled types
import Plotly from "plotly.js-cartesian-dist-min";

const Plot = createPlotlyComponent(Plotly) as React.ComponentType<{
  data: unknown[];
  layout?: Record<string, unknown>;
  config?: Record<string, unknown>;
  style?: React.CSSProperties;
  className?: string;
  useResizeHandler?: boolean;
  onInitialized?: (figure: unknown, graphDiv: HTMLDivElement) => void;
  onUpdate?: (figure: unknown, graphDiv: HTMLDivElement) => void;
}>;

export default Plot;
