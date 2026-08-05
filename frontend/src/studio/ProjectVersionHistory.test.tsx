import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { SaasProjectVersion } from './types';
import { ProjectVersionHistory } from './ProjectVersionHistory';

const versions: SaasProjectVersion[] = [
  {
    id: 'version-2',
    project_id: 'project-1',
    ordinal: 2,
    kind: 'refinement',
    change_request: 'Сделай приветствие короче',
    parent_version_id: 'version-1',
    run_id: 'run-2',
    artifact_id: 'artifact-2',
    artifact_revision: 7,
    refinable: true,
    created_at: '2026-07-30T09:00:00Z',
  },
  {
    id: 'version-1',
    project_id: 'project-1',
    ordinal: 1,
    kind: 'initial',
    change_request: null,
    parent_version_id: null,
    run_id: 'run-1',
    artifact_id: 'artifact-1',
    artifact_revision: 3,
    refinable: true,
    created_at: '2026-07-29T08:00:00Z',
  },
];

afterEach(cleanup);

describe('ProjectVersionHistory', () => {
  it('identifies active and selected versions and exposes separate select/restore actions', () => {
    const onSelect = vi.fn();
    const onRestore = vi.fn();

    render(
      <ProjectVersionHistory
        versions={versions}
        activeVersionId="version-2"
        selectedVersionId="version-1"
        mutationPending={false}
        running={false}
        onSelect={onSelect}
        onRestore={onRestore}
      />,
    );

    expect(screen.getByRole('region', { name: 'История версий' })).toBeVisible();
    expect(screen.getByText('Версия 2')).toBeVisible();
    expect(screen.queryByText(/Ревизия артефакта/)).not.toBeInTheDocument();
    expect(screen.getByText('Доработка')).toBeVisible();
    expect(screen.getByText('Сделай приветствие короче')).toBeVisible();
    expect(screen.getByText('Текущая версия')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Просмотреть версию 1' })).toHaveAttribute(
      'aria-current',
      'true',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Просмотреть версию 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Восстановить версию 1' }));

    expect(onSelect).toHaveBeenCalledWith('version-2');
    expect(onRestore).toHaveBeenCalledWith('version-1');
    expect(screen.queryByRole('button', { name: 'Восстановить версию 2' })).not.toBeInTheDocument();
  });

  it('reveals and collapses a complete long change request', () => {
    const longRequest = 'Сделай ответы короче, добавь больше воздуха и сохрани спокойный тон бренда. '
      .repeat(6)
      .slice(0, 320);

    render(
      <ProjectVersionHistory
        versions={[{ ...versions[0], change_request: longRequest }]}
        activeVersionId="version-2"
        selectedVersionId="version-2"
        mutationPending={false}
        running={false}
        onSelect={vi.fn()}
        onRestore={vi.fn()}
      />,
    );

    expect(screen.queryByText(longRequest)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Показать полностью' }));
    expect(screen.getByText(longRequest)).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Свернуть' }));
    expect(screen.queryByText(longRequest)).not.toBeInTheDocument();
  });
});
