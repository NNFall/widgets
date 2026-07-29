const CAMPAIGN_KEYS = [
  'utm_source',
  'utm_medium',
  'utm_campaign',
  'utm_term',
  'utm_content',
] as const;

export type Campaign = Partial<Record<(typeof CAMPAIGN_KEYS)[number], string>>;

const REGISTERED_CAMPAIGN_VALUES: Record<(typeof CAMPAIGN_KEYS)[number], ReadonlySet<string>> = {
  utm_source: new Set(['email', 'google', 'partner', 'referral', 'telegram', 'vk', 'yandex']),
  utm_medium: new Set([
    'affiliate',
    'cpc',
    'display',
    'email',
    'organic',
    'paid-search',
    'paid-social',
    'referral',
    'social',
  ]),
  utm_campaign: new Set([
    'beta',
    'cmp_0123456789abcdef',
    'first-widget',
    'launch',
    'product-launch',
    'summer',
  ]),
  utm_term: new Set(['ai', 'summer_sale', 'widgets']),
  utm_content: new Set(['hero', 'launch-post', 'studio']),
};

function registeredCampaignValue(key: (typeof CAMPAIGN_KEYS)[number], value: string): string | null {
  const normalized = value.trim().toLocaleLowerCase();
  if (REGISTERED_CAMPAIGN_VALUES[key].has(normalized)) return normalized;
  return null;
}

export function campaignFromSearch(search = window.location.search): Campaign {
  const source = new URLSearchParams(search);
  const campaign: Campaign = {};
  for (const key of CAMPAIGN_KEYS) {
    const rawValue = source.get(key);
    const value = rawValue ? registeredCampaignValue(key, rawValue) : null;
    if (value) campaign[key] = value;
  }
  return campaign;
}

export function studioHref(search = window.location.search): string {
  const params = new URLSearchParams(campaignFromSearch(search));
  const query = params.toString();
  return query ? `/studio?${query}` : '/studio';
}

export function studioHrefWithDraft(draftId: string): string {
  return `/studio?${new URLSearchParams({ draft: draftId }).toString()}`;
}
