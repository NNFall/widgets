export function mount(root) {
  const form = root.querySelector('.kaigo-composer--soft');
  const submit = event => { event.preventDefault(); const input = form?.querySelector('textarea'); const text = input?.value.trim(); if (text) form.dispatchEvent(new CustomEvent('kaigo:message', { bubbles: true, detail: { text } })); if (input) input.value = ''; };
  form?.addEventListener('submit', submit);
  return () => form?.removeEventListener('submit', submit);
}
