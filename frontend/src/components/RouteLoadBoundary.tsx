import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  failed: boolean;
}

export function RouteLoadingStatus(): React.JSX.Element {
  return <p role="status" aria-live="polite">Loading page…</p>;
}

export class RouteLoadBoundary extends Component<Props, State> {
  public override state: State = { failed: false };

  public static getDerivedStateFromError(): State {
    return { failed: true };
  }

  public override render(): ReactNode {
    if (this.state.failed) {
      return (
        <section className="error-summary" role="alert" aria-labelledby="route-load-error-title">
          <h1 id="route-load-error-title">This page could not be loaded</h1>
          <p>Your records were not changed. Reload the page to try again.</p>
          <button type="button" onClick={() => { window.location.reload(); }}>Reload page</button>
        </section>
      );
    }
    return this.props.children;
  }
}
