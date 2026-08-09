import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthContext, type AuthContextValue } from "../auth/context";
import { LoginPage } from "./LoginPage";

describe("Login page MFA", () => {
  it("submits an authenticator or recovery code with the password", async () => {
    const signIn = vi.fn().mockResolvedValue(undefined);
    const auth: AuthContextValue = { status: "anonymous", session: null, signIn, signOut: vi.fn() };
    render(<AuthContext.Provider value={auth}><MemoryRouter initialEntries={["/login"]}><Routes><Route path="/login" element={<LoginPage />} /><Route path="/today" element={<p>Today</p>} /></Routes></MemoryRouter></AuthContext.Provider>);

    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "owner@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "synthetic-password" } });
    fireEvent.change(screen.getByLabelText("Authenticator or recovery code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => { expect(signIn).toHaveBeenCalledWith("owner@example.test", "synthetic-password", "123456"); });
  });
});
