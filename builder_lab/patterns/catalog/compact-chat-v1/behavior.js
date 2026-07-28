export function mount(root) {
  const close = root.querySelector('.kaigo-shell__close');
  const onClose = () => root.dispatchEvent(new CustomEvent('kaigo:close', { bubbles: true }));
  close?.addEventListener('click', onClose);
  return () => close?.removeEventListener('click', onClose);
}
