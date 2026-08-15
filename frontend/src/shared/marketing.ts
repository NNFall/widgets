const ARCHIVE_VERSION_PATTERN = /^v\d+$/;
const ARCHIVE_PATH_PATTERN = /^\/frontend\/(v\d+)(?:\/|$)/;

function archiveVersion(pathname: string, search: string): string | null {
  const pathMatch = pathname.match(ARCHIVE_PATH_PATTERN)?.[1];
  if (pathMatch && ARCHIVE_VERSION_PATTERN.test(pathMatch)) return pathMatch;

  const queryVersion = new URLSearchParams(search).get('archive');
  return queryVersion && ARCHIVE_VERSION_PATTERN.test(queryVersion) ? queryVersion : null;
}

/**
 * Builds an internal marketing URL that stays inside an isolated archive when
 * the current preview is served below /frontend/vN/.
 */
export function marketingHref(
  target: string,
  pathname = typeof window === 'undefined' ? '/' : window.location.pathname,
  search = typeof window === 'undefined' ? '' : window.location.search,
): string {
  if (!target.startsWith('/') && !target.startsWith('#')) return target;

  const version = archiveVersion(pathname, search);
  if (!version) return target;

  const archiveRoot = `/frontend/${version}`;
  if (target.startsWith('#')) return `${archiveRoot}/${target}`;
  return target === '/' ? `${archiveRoot}/` : `${archiveRoot}${target}`;
}
