export function mount(root) {
  const motion = root.querySelector('.kaigo-motion--spring');
  if (motion) requestAnimationFrame(() => { motion.dataset.state = 'opening'; });
  return () => { if (motion) motion.dataset.state = 'closing'; };
}
