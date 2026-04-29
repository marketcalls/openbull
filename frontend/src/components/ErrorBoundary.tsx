/**
 * Generic error boundary used to wrap chart-heavy components.
 *
 * Plotly + react-plotly.js can throw on edge-case data shapes (NaN
 * everywhere, mismatched array lengths after a debounce race, etc.)
 * and an uncaught render error in a tab content area would unmount
 * the whole Strategy Builder page — losing leg state, snapshot, and
 * forcing a re-fetch. Boundary keeps the rest of the page alive and
 * shows a small fallback the user can dismiss with "Retry".
 *
 * Class component because React 19 still requires that for the
 * componentDidCatch / getDerivedStateFromError hooks. Tiny by design.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

interface Props {
  children: ReactNode;
  /** Optional context label shown in the fallback so the user can tell
   *  *which* chart blew up if there are several on the page. */
  label?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface to console — surface-only, no telemetry pipe in OpenBull yet.
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught", this.props.label ?? "", error, info);
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 py-10 text-center">
          <p className="text-sm font-medium text-destructive">
            {this.props.label ? `${this.props.label} crashed` : "Component crashed"}
          </p>
          <p className="max-w-md text-xs text-muted-foreground">
            {this.state.error.message || "An unexpected rendering error occurred."}
          </p>
          <Button variant="outline" size="sm" onClick={this.reset}>
            Retry
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}
