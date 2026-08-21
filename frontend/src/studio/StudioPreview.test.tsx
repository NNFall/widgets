import { act, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StudioPreview } from './StudioPreview';

const baseProps = {
  runId: 'run-preview',
  revision: 4,
  artDirection: 'Спокойный консультант в стиле сайта',
  qualityStatus: 'verified',
  onViewportChange: vi.fn(),
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function mockCanvasGeometry(width: number, height: number) {
  const geometry = { width, height };
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function clientWidth(
    this: HTMLElement,
  ) {
    return this.classList.contains('studio-preview__canvas') ? geometry.width : 0;
  });
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function clientHeight(
    this: HTMLElement,
  ) {
    return this.classList.contains('studio-preview__canvas') ? geometry.height : 0;
  });
  vi.stubGlobal('getComputedStyle', vi.fn(() => ({
    paddingLeft: '12px',
    paddingRight: '12px',
    paddingTop: '12px',
    paddingBottom: '12px',
  })));
  return geometry;
}

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

  it('fits the 390 by 844 device inside both available canvas dimensions', () => {
    mockCanvasGeometry(360, 516);
    const { container, rerender } = render(
      <StudioPreview {...baseProps} viewport="mobile" />,
    );
    const canvas = container.querySelector<HTMLElement>('.studio-preview__canvas');
    const slot = container.querySelector<HTMLElement>('.studio-preview__device-slot');
    expect(slot).toHaveAttribute('data-preview-scale', '0.5829');
    expect(Number.parseFloat(slot?.style.height ?? '')).toBeLessThanOrEqual(492);
    expect(Number.parseFloat(slot?.style.width ?? '')).toBeLessThanOrEqual(336);
    expect(canvas?.style.minHeight).toBe('');

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

  it('recalculates the height-constrained scale when ResizeObserver reports a shorter canvas', () => {
    const geometry = mockCanvasGeometry(390, 720);
    let resizeCallback: ResizeObserverCallback | null = null;
    const disconnect = vi.fn();
    vi.stubGlobal('ResizeObserver', class ResizeObserverMock {
      constructor(callback: ResizeObserverCallback) {
        resizeCallback = callback;
      }

      observe() {}

      disconnect() {
        disconnect();
      }
    });

    const { container, unmount } = render(
      <StudioPreview {...baseProps} viewport="mobile" />,
    );
    const slot = container.querySelector<HTMLElement>('.studio-preview__device-slot');
    expect(slot).toHaveAttribute('data-preview-scale', '0.8246');

    geometry.height = 460;
    act(() => {
      resizeCallback?.([], {} as ResizeObserver);
    });

    expect(slot).toHaveAttribute('data-preview-scale', '0.5166');
    expect(Number.parseFloat(slot?.style.height ?? '')).toBeLessThanOrEqual(436);
    unmount();
    expect(disconnect).toHaveBeenCalledOnce();
  });
});
