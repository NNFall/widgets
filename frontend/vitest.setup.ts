import '@testing-library/jest-dom/vitest';

class IntersectionObserverMock implements IntersectionObserver {
  readonly root = null;
  readonly rootMargin = '0px';
  readonly scrollMargin = '0px';
  readonly thresholds = [0];

  constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
    void callback;
    void options;
  }

  disconnect() {}
  observe() {}
  takeRecords(): IntersectionObserverEntry[] { return []; }
  unobserve() {}
}

if (!globalThis.IntersectionObserver) {
  globalThis.IntersectionObserver = IntersectionObserverMock;
}

window.scrollTo = () => undefined;
