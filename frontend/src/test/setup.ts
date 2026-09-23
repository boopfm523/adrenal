import "@testing-library/jest-dom/vitest";

Object.defineProperty(window, "matchMedia", {
  configurable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Node's own localStorage global shadows jsdom's and is inert without a backing
// file, so anything that remembers a choice across page views needs this.
class MemoryStorage implements Storage {
  #entries = new Map<string, string>();
  get length(): number { return this.#entries.size; }
  clear(): void { this.#entries.clear(); }
  getItem(key: string): string | null { return this.#entries.get(key) ?? null; }
  key(index: number): string | null { return [...this.#entries.keys()][index] ?? null; }
  removeItem(key: string): void { this.#entries.delete(key); }
  setItem(key: string, value: string): void { this.#entries.set(key, value); }
}

Object.defineProperty(window, "localStorage", {
  configurable: true,
  value: new MemoryStorage(),
});

class ResizeObserverStub implements ResizeObserver {
  observe(): void { return undefined; }
  unobserve(): void { return undefined; }
  disconnect(): void { return undefined; }
}

Object.defineProperty(globalThis, "ResizeObserver", {
  configurable: true,
  value: ResizeObserverStub,
});

afterEach(() => {
  vi.restoreAllMocks();
});
