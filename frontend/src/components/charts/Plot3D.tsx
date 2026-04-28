// Plot3D component — uses the full plotly.js-dist-min bundle (~600 KB gz)
// to support 3D surface / scatter3d traces, paired with react-plotly.js.
// Only imported by tools that actually need 3D (e.g. /tools/volsurface), so
// the cartesian-only bundle stays in place for the rest of the app.

// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — react-plotly.js/factory has no bundled types
import * as FactoryNS from "react-plotly.js/factory";
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — plotly.js-dist-min has no bundled types
import * as PlotlyNS from "plotly.js-dist-min";

function unwrap<T>(mod: unknown): T {
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
  (plotly: unknown) => React.ComponentType<Plot3DComponentProps>
>(FactoryNS);
const Plotly = unwrap<unknown>(PlotlyNS);

if (typeof createPlotlyComponent !== "function") {
  throw new Error(
    "react-plotly.js/factory did not export a function — check install / bundler interop."
  );
}

interface Plot3DComponentProps {
  data: unknown[];
  layout?: Record<string, unknown>;
  config?: Record<string, unknown>;
  style?: React.CSSProperties;
  className?: string;
  useResizeHandler?: boolean;
  onInitialized?: (figure: unknown, graphDiv: HTMLDivElement) => void;
  onUpdate?: (figure: unknown, graphDiv: HTMLDivElement) => void;
}

const Plot3D = createPlotlyComponent(Plotly) as React.ComponentType<Plot3DComponentProps>;

export default Plot3D;
