import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';

import { StudioDrawer } from './StudioDrawer';

afterEach(() => {
  cleanup();
  document.body.style.overflow = '';
});

function DrawerHarness() {
  const [open, setOpen] = useState(false);
  const [showOpener, setShowOpener] = useState(true);

  return (
    <div>
      <header data-testid="header-surface">Studio header</header>
      <main data-testid="main-surface">
        {showOpener && (
          <button type="button" onClick={() => setOpen(true)}>
            Открыть панель
          </button>
        )}
      </main>
      <StudioDrawer
        open={open}
        title="Панель"
        description="Описание панели"
        onClose={() => setOpen(false)}
      >
        <button type="button">Первый контроль</button>
        <a href="/help">Последний контроль</a>
        <button type="button" onClick={() => setShowOpener(false)}>
          Убрать открыватель
        </button>
      </StudioDrawer>
    </div>
  );
}

describe('StudioDrawer', () => {
  it('focuses close, traps forward and reverse Tab, restores inert surfaces and body overflow', async () => {
    const user = userEvent.setup();
    document.body.style.overflow = 'scroll';
    render(<DrawerHarness />);

    const header = screen.getByTestId('header-surface');
    header.setAttribute('inert', 'preserved');
    const main = screen.getByTestId('main-surface');
    const opener = screen.getByRole('button', { name: 'Открыть панель' });
    await user.click(opener);

    const dialog = screen.getByRole('dialog', { name: 'Панель' });
    const close = within(dialog).getByRole('button', { name: 'Закрыть панель' });
    const first = within(dialog).getByRole('button', { name: 'Первый контроль' });
    const last = within(dialog).getByRole('link', { name: 'Последний контроль' });
    const remove = within(dialog).getByRole('button', { name: 'Убрать открыватель' });

    expect(close).toHaveFocus();
    expect(header).toHaveAttribute('inert', '');
    expect(main).toHaveAttribute('inert', '');
    expect(document.body.style.overflow).toBe('hidden');

    await user.tab();
    expect(first).toHaveFocus();
    await user.tab();
    expect(last).toHaveFocus();
    await user.tab();
    expect(remove).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(remove).toHaveFocus();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Панель' })).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(header).toHaveAttribute('inert', 'preserved');
    expect(main).not.toHaveAttribute('inert');
    expect(document.body.style.overflow).toBe('scroll');
  });

  it('keeps Tab inside a drawer with only its close control enabled', async () => {
    const user = userEvent.setup();
    function SingleControlHarness() {
      const [open, setOpen] = useState(true);
      return (
        <StudioDrawer open={open} title="Одна кнопка" onClose={() => setOpen(false)}>
          <button type="button" disabled>Недоступный контроль</button>
        </StudioDrawer>
      );
    }

    render(<SingleControlHarness />);
    const dialog = screen.getByRole('dialog', { name: 'Одна кнопка' });
    const close = within(dialog).getByRole('button', { name: 'Закрыть панель' });
    expect(close).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();
    await user.tab({ shift: true });
    expect(close).toHaveFocus();
  });

  it('does not restore focus to an opener that was detached while the drawer was open', async () => {
    const user = userEvent.setup();
    render(<DrawerHarness />);

    const opener = screen.getByRole('button', { name: 'Открыть панель' });
    await user.click(opener);
    const openerFocus = vi.spyOn(opener, 'focus');
    await user.click(screen.getByRole('button', { name: 'Убрать открыватель' }));
    expect(opener.isConnected).toBe(false);

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Панель' })).not.toBeInTheDocument();
    expect(openerFocus).not.toHaveBeenCalled();
    expect(document.activeElement).not.toBe(opener);
  });
});
