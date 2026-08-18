import { X } from '@phosphor-icons/react';
import { type ReactNode, useEffect, useId, useRef } from 'react';

type StudioDrawerProps = {
  open: boolean;
  title: string;
  description?: string;
  variant?: 'drawer' | 'modal';
  children: ReactNode;
  onClose: () => void;
};

export function StudioDrawer({
  open,
  title,
  description,
  variant = 'drawer',
  children,
  onClose,
}: StudioDrawerProps) {
  const titleId = useId();
  const descriptionId = useId();
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    closeButtonRef.current?.focus();
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div
      className={`studio-drawer${variant === 'modal' ? ' studio-drawer--modal' : ''}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className={`studio-drawer__panel${variant === 'modal' ? ' studio-drawer__panel--modal' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
      >
        <header className="studio-drawer__header">
          <div>
            <h2 id={titleId}>{title}</h2>
            {description && <p id={descriptionId}>{description}</p>}
          </div>
          <button ref={closeButtonRef} type="button" onClick={onClose} aria-label="Закрыть панель">
            <X aria-hidden size={20} weight="bold" />
          </button>
        </header>
        <div className="studio-drawer__body">{children}</div>
      </section>
    </div>
  );
}
