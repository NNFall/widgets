import { describe, expect, it } from 'vitest';

import { campaignFromSearch } from './campaign';

describe('campaign attribution contract', () => {
  it.each([
    'customer-14155552671',
    'lead-550e8400-e29b-41d4-a716-446655440000',
    'visitor-aB3dE5fG7hJ9kL2mN4pQ6rS8tV',
    'AKIAIOSFODNN7EXAMPLE',
  ])('drops unknown or secret-like raw dimensions: %s', (unsafeValue) => {
    const search = new URLSearchParams({
      utm_source: unsafeValue,
      utm_medium: unsafeValue,
      utm_campaign: unsafeValue,
      utm_term: unsafeValue,
      utm_content: unsafeValue,
    });

    expect(campaignFromSearch(`?${search.toString()}`)).toEqual({});
  });

  it('keeps only registered dimensions and opaque server campaign ids', () => {
    expect(campaignFromSearch(
      '?utm_source=Telegram&utm_medium=SOCIAL'
      + '&utm_campaign=cmp_0123456789abcdef&utm_term=widgets&utm_content=hero',
    )).toEqual({
      utm_source: 'telegram',
      utm_medium: 'social',
      utm_campaign: 'cmp_0123456789abcdef',
      utm_term: 'widgets',
      utm_content: 'hero',
    });
  });
});
