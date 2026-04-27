// Plot component that pairs the smaller plotly.js-cartesian-dist-min bundle
// (~200 KB gz; bar/scatter/line traces only — no candlestick/maps/3d) with the
// react wrapper from react-plotly.js. Centralised so other tools share one
// Plotly bundle instead of importing the heavy default.
//
// Both `react-plotly.js/factory` and `plotly.js-cartesian-dist-min` are CJS,
// and Vite's dev-mode interop occasionally hands back the namespace object
// instead of the default export — `import x from "..."` ends up as
// `{ default: <fn> }` rather than `<fn>`. We unwrap defensively to handle
// every shape (default export, namespace, .default.default).

// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — react-plotly.js/factory has no bundled types
import * as FactoryNS from "react-plotly.js/factory";
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — plotly.js-cartesian-dist-min has no bundled types
import * as PlotlyNS from "plotly.js-cartesian-dist-min";

function unwrap<T>(mod: unknown): T {
  // CJS modules under Vite can land as { default: x }, { default: { default: x } },
  // or x directly. Walk down `.default` while it's still wrapping a function.
  let cur: unknown = mod;
  while (cur && typeof cur === "object" && "default" in (cur as Record<string, unknown>)) {
    const inner = (cur as { default: unknown }).default;
    if (inner === cur) break;
    cur = inner;
    if (typeof cur === "function") break;
  }
  return cur as T;
}

const createPlotlyComponent = unwrap<
  (plotly: unknown) => React.ComponentType<PlotComponentProps>
>(FactoryNS);

const Plotly = unwrap<unknown>(PlotlyNS);

if (typeof createPlotlyComponent !== "function") {
  // Surface a clear error rather than the cryptic
  // "createPlotlyComponent is not a function" stack from inside the factory.
  throw new Error(
    "react-plotly.js/factory did not export a function — check the install / bundler interop."
  );
}

interface PlotComponentProps {
  data: unknown[];
  layout?: Record<string, unknown>;
  config?: Record<string, unknown>;
  style?: React.CSSProperties;
  className?: string;
  useResizeHandler?: boolean;
  onInitialized?: (figure: unknown, graphDiv: HTMLDivElement) => void;
  onUpdate?: (figure: unknown, graphDiv: HTMLDivElement) => void;
}

const Plot = createPlotlyComponent(Plotly) as React.ComponentType<PlotComponentProps>;

export default Plot;
