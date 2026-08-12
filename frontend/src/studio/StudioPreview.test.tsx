import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { StudioPreview } from './StudioPreview';

const baseProps = {
  runId: 'run-preview',
  revision: 4,
  artDirection: 'Спокойный консультант в стиле сайта',
  qualityStatus: 'verified',
  onViewportChange: vi.fn(),
};

describe('StudioPreview', () => {
  it('publishes the audit-equivalent dimensions of the selected preview device', () => {
    const { container, rerender } = render(
      <StudioPreview {...baseProps} viewport="desktop" />,
    );

    const desktopDevice = container.querySelector('.studio-preview__device');
    expect(desktopDevice).toHaveAttribute('data-viewport', 'desktop');
    expect(desktopDevice).toHaveAttribute('data-viewport-width', '1920');
    expect(desktopDevice).toHaveAttribute('data-viewport-height', '1080');

    rerender(<StudioPreview {...baseProps} viewport="mobile" />);

    const mobileDevice = container.querySelector('.studio-preview__device');
    expect(mobileDevice).toHaveAttribute('data-viewport', 'mobile');
    expect(mobileDevice).toHaveAttribute('data-viewport-width', '390');
    expect(mobileDevice).toHaveAttribute('data-viewport-height', '844');
    const mobileFrame = container.querySelector('iframe');
    expect(container.querySelector('.studio-preview__device-slot')).toBeInTheDocument();
    expect(mobileFrame).toHaveAttribute('width', '390');
    expect(mobileFrame).toHaveAttribute('height', '844');
    expect(mobileFrame).toHaveAttribute('sandbox', 'allow-scripts');
  });

  it('fits the mobile reference viewport when a preview appears after the empty state', () => {
    const { container, rerender } = render(
      <StudioPreview {...baseProps} runId={null} revision={null} viewport="mobile" />,
    );

    expect(container.querySelector('.studio-preview__device-slot')).not.toBeInTheDocument();

    rerender(<StudioPreview {...baseProps} viewport="mobile" />);

    expect(container.querySelector('.studio-preview__device-slot')).toHaveAttribute(
      'data-preview-scale',
      '1.0000',
    );
  });

  it('clears mobile sizing when the preview disappears before desktop reset', () => {
    const { container, rerender } = render(
      <StudioPreview {...baseProps} viewport="mobile" />,
    );
    const canvas = container.querySelector<HTMLElement>('.studio-preview__canvas');
    expect(canvas?.style.minHeight).not.toBe('');

    rerender(
      <StudioPreview
        {...baseProps}
        runId={null}
        revision={null}
        viewport="desktop"
      />,
    );

    expect(canvas?.style.minHeight).toBe('');
  });
});
