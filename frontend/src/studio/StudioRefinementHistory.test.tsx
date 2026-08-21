import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { StudioRefinementHistory } from './StudioRefinementHistory';
import type { RefinementConversationEntry } from './refinementConversation';

function entry(status: RefinementConversationEntry['status']): RefinementConversationEntry {
  return {
    id: `entry-${status}`,
    runId: `run-${status}`,
    changeRequest: 'Обнови анимацию закрытия',
    status,
    versionNumber: status === 'completed' ? 2 : null,
    assistantTitle: status === 'completed' ? 'Готово — версия 2' : 'Доработка выполняется',
    assistantMessage: status === 'completed'
      ? 'Анимация обновлена.'
      : 'Kaigo проверяет результат.',
  };
}

describe('StudioRefinementHistory', () => {
  it('uses ordered-list semantics and announces only the active refinement', () => {
    render(<StudioRefinementHistory entries={[entry('completed'), entry('running')]} />);

    expect(screen.getByRole('list', { name: 'История доработок' })).toBeVisible();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Доработка выполняетсяKaigo проверяет результат.',
    );
    expect(screen.getByText('Готово — версия 2').closest('[role="status"]')).toBeNull();
  });
});
