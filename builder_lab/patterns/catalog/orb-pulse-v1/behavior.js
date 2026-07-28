export function mount(root, options = {}) {
  const launcher = root.querySelector('.kaigo-launcher--orb');
  if (!launcher) return () => {};
  const delay = Number(options.attentionDelayMs || 15000);
  const timer = setTimeout(() => { launcher.dataset.attention = 'true'; }, delay);
  return () => clearTimeout(timer);
}
