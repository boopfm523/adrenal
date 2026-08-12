import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "../auth/context";
import { HealthCurveProvider } from "../components/HealthCurveProvider";
import { LoginPage } from "./LoginPage";

describe("Login page", () => {
  it("submits only the owner email and password", async () => {
    const signIn = vi.fn().mockResolvedValue(undefined);
    const auth: AuthContextValue = { status: "anonymous", session: null, signIn, signOut: vi.fn() };
    render(<HealthCurveProvider><AuthContext.Provider value={auth}><MemoryRouter initialEntries={["/login"]}><Routes><Route path="/login" element={<LoginPage />} /><Route path="/today" element={<p>Today</p>} /></Routes></MemoryRouter></AuthContext.Provider></HealthCurveProvider>);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "synthetic-password" } });
    expect(screen.queryByText(/authenticator|recovery code|passkey/i)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Sign in to HealthCurve.ai" })).toBeVisible();
    expect(screen.getByRole("img", { name: "HealthCurve.ai" })).toBeVisible();
    expect(screen.getByText(/Artificial intelligence for adrenal insufficiency/)).toBeVisible();
    expect(document.title).toBe("Sign in · HealthCurve.ai");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => { expect(signIn).toHaveBeenCalledWith("owner@example.test", "synthetic-password"); });
  });
});
