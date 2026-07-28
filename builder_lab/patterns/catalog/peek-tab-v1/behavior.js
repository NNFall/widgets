export function mount(root, options = {}) {
  const launcher = root.querySelector('.kaigo-launcher--peek');
  if (!launcher) return () => {};
  const timer = setTimeout(() => { launcher.dataset.attention = 'true'; }, Number(options.attentionDelayMs || 15000));
  return () => clearTimeout(timer);
}
