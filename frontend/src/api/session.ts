export interface SessionUser {
  email: string;
  displayName: string | null;
  defaultTimezone: string;
  mfaEnabled?: boolean;
}

export interface ActiveSession {
  csrfToken: string;
  user: SessionUser;
}

type SessionListener = (session: ActiveSession | null) => void;

class SessionStore {
  readonly #listeners = new Set<SessionListener>();
  #session: ActiveSession | null = null;

  get(): ActiveSession | null {
    return this.#session;
  }

  set(session: ActiveSession): void {
    this.#session = session;
    this.#emit();
  }

  clear(): void {
    if (this.#session === null) return;
    this.#session = null;
    this.#emit();
  }

  subscribe(listener: SessionListener): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  #emit(): void {
    for (const listener of this.#listeners) listener(this.#session);
  }
}

export const sessionStore = new SessionStore();
