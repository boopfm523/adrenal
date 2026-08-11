import { render, screen } from "@testing-library/react";

import { RouteLoadBoundary, RouteLoadingStatus } from "./RouteLoadBoundary";

function BrokenRoute(): React.JSX.Element {
  throw new Error("Synthetic route import failure");
}

describe("RouteLoadBoundary", () => {
  it("announces route loading without interrupting assistive technology", () => {
    render(<RouteLoadingStatus />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading page…");
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
  });

  it("shows a safe accessible recovery action without raw error details", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<RouteLoadBoundary><BrokenRoute /></RouteLoadBoundary>);

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("This page could not be loaded");
    expect(alert).toHaveTextContent("Your records were not changed");
    expect(alert).not.toHaveTextContent("Synthetic route import failure");
    expect(screen.getByRole("button", { name: "Reload page" })).toBeVisible();
    error.mockRestore();
  });
});
