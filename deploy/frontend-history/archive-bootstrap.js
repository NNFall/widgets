(() => {
  const marker = document.currentScript;
  const archiveVersion = marker?.dataset.archiveVersion || 'unknown';
  const archiveView = marker?.dataset.archiveView || 'landing';
  const apiPrefix = '/frontend-preview-api/api/';
  const builderPrefix = '/frontend-preview-api/';

  const rewriteApiUrl = (input) => {
    const original = input instanceof Request ? input.url : String(input);
    const url = new URL(original, window.location.origin);

    if (url.origin === window.location.origin && url.pathname.startsWith('/api/')) {
      url.pathname = `${apiPrefix}${url.pathname.slice('/api/'.length)}`;
      return url.toString();
    }
    if (url.origin === window.location.origin && url.pathname.startsWith('/builder/')) {
      url.pathname = `${builderPrefix}${url.pathname.slice('/builder/'.length)}`;
      return url.toString();
    }

    return input;
  };

  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const rewritten = rewriteApiUrl(input);
    if (input instanceof Request && rewritten !== input) {
      return nativeFetch(new Request(rewritten, input), init);
    }
    return nativeFetch(rewritten, init);
  };

  if (window.EventSource) {
    const NativeEventSource = window.EventSource;
    window.EventSource = class ArchiveEventSource extends NativeEventSource {
      constructor(url, options) {
        super(rewriteApiUrl(url), options);
      }
    };
  }

  if (archiveView !== 'landing') {
    const query = archiveView === 'studio'
      ? `project=manual-friendly-studio&archive=${encodeURIComponent(archiveVersion)}`
      : `archive=${encodeURIComponent(archiveVersion)}`;
    window.history.replaceState(null, '', `/${archiveView}?${query}`);
  }
})();
