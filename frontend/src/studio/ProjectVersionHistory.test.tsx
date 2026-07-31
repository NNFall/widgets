import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

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
    expect(screen.getByText('Ревизия артефакта 7')).toBeVisible();
    expect(screen.getByText('Ревизия артефакта 3')).toBeVisible();
    expect(screen.getByText('Доработка')).toBeVisible();
    expect(screen.getByText('Сделай приветствие короче')).toBeVisible();
    expect(screen.getByText('Активная версия')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Просмотреть версию 1' })).toHaveAttribute(
      'aria-current',
      'true',
    );

    fireEvent.click(screen.getByRole('button', { name: 'Просмотреть версию 2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Восстановить версию 1' }));

    expect(onSelect).toHaveBeenCalledWith('version-2');
    expect(onRestore).toHaveBeenCalledWith('version-1');
    expect(screen.getByRole('button', { name: 'Восстановить версию 2' })).toBeDisabled();
  });
});
