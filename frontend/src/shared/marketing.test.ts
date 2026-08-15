import { describe, expect, it } from 'vitest';

import { marketingHref } from './marketing';

describe('marketingHref', () => {
  it('keeps ordinary internal links at the public root', () => {
    expect(marketingHref('/privacy/', '/', '')).toBe('/privacy/');
    expect(marketingHref('#contact', '/', '')).toBe('#contact');
  });

  it('prefixes safe archive links from an archive pathname or query marker', () => {
    expect(marketingHref('/privacy/', '/frontend/v11/privacy/', '')).toBe('/frontend/v11/privacy/');
    expect(marketingHref('/personal-data-consent/', '/frontend/v11/', '')).toBe('/frontend/v11/personal-data-consent/');
    expect(marketingHref('#contact', '/frontend/v11/', '')).toBe('/frontend/v11/#contact');
    expect(marketingHref('/terms/', '/', '?archive=v11')).toBe('/frontend/v11/terms/');
  });

  it('does not trust malformed archive versions or arbitrary query values', () => {
    expect(marketingHref('/privacy/', '/frontend/v11-preview/privacy/', '')).toBe('/privacy/');
    expect(marketingHref('/privacy/', '/', '?archive=v11-preview')).toBe('/privacy/');
    expect(marketingHref('/privacy/', '/', '?archive=https%3A%2F%2Fevil.test')).toBe('/privacy/');
  });
});
