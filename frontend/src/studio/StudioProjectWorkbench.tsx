import {
  ArrowsClockwise,
  CaretDown,
  CheckCircle,
  ClockCounterClockwise,
  CloudArrowUp,
  FolderOpen,
  PaperPlaneTilt,
  Plus,
  StopCircle,
  UserCircle,
} from '@phosphor-icons/react';
import { type FormEvent, type ReactNode, useCallback, useState } from 'react';

import { KaigoLogo } from '../shared/KaigoLogo';
import { ProjectVersionHistory } from './ProjectVersionHistory';
import { StudioAccountPanel } from './StudioAccountPanel';
import { StudioContactPanel } from './StudioContactPanel';
import { StudioDrawer } from './StudioDrawer';
import { StudioLibrary } from './StudioLibrary';
import { StudioPreview } from './StudioPreview';
import { StudioProgress } from './StudioProgress';
import { StudioTimeline } from './StudioTimeline';
import { UpgradeGate } from './UpgradeGate';
import type { PreviewViewport } from './types';
import type { BuilderRunController } from './useBuilderRun';

type WorkbenchDrawer = 'projects' | 'versions' | 'account' | 'contact' | 'publication' | null;
type MobilePane = 'chat' | 'preview';

type StudioProjectWorkbenchProps = {
  controller: BuilderRunController;
  projectId: string;
  domain: string;
  sourceUrl: string;
  brief: string;
  errorNotice?: ReactNode;
  onOpenProject: (projectId: string) => void;
  onOpenStudioHome: (focusNewWidget: boolean) => void;
};

function isReadyQuality(status: string | undefined) {
  return status === 'verified' || status === 'accepted';
}

function snapshotProgress(controller: BuilderRunController) {
  if (!controller.snapshot) return 0;
  if (controller.snapshot.status === 'completed') return 100;
  const value = controller.snapshot.progress;
  return typeof value === 'number' ? Math.max(0, Math.min(100, Math.round(value))) : 0;
}

export function StudioProjectWorkbench({
  controller,
  projectId,
  domain,
  sourceUrl,
  brief,
  errorNotice,
  onOpenProject,
  onOpenStudioHome,
}: StudioProjectWorkbenchProps) {
  const [drawer, setDrawer] = useState<WorkbenchDrawer>(null);
  const [mobilePane, setMobilePane] = useState<MobilePane>('chat');
  const [viewport, setViewport] = useState<PreviewViewport>('desktop');
  const [refinement, setRefinement] = useState('');
  const closeDrawer = useCallback(() => setDrawer(null), []);

  const artifact = controller.selectedArtifact;
  const activeVersionId = controller.activeVersionId ?? controller.project?.active_version_id ?? null;
  const selectedVersion = controller.selectedVersion;
  const status = controller.snapshot?.status ?? null;
  const running = status === 'created' || status === 'queued' || status === 'running';
  const qualityStatus = artifact?.quality_status ?? controller.snapshot?.quality_status ?? 'pending';
  const previewRevision = artifact?.revision ?? selectedVersion?.artifact_revision ?? null;
  const displayedVersionNumber = selectedVersion?.ordinal
    ?? controller.versions.find((version) => version.id === activeVersionId)?.ordinal
    ?? previewRevision;
  const freeResultReady = controller.versionsAvailable
    ? Boolean(selectedVersion && artifact)
    : status === 'completed'
      && isReadyQuality(controller.snapshot?.quality_status)
      && artifact?.source === 'accepted_artifact'
      && Boolean(artifact.id);
  const refinable = controller.versionsAvailable
    ? Boolean(
        artifact
        && selectedVersion?.id === activeVersionId
        && selectedVersion.refinable
        && !running,
      )
    : status === 'completed' && Boolean(artifact);

  const submitRefinement = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const message = refinement.trim();
    if (!message || !refinable || controller.mutationPending) return;
    setRefinement('');
    void controller.refineRun(message);
  };

  const selectVersion = async (versionId: string) => {
    await controller.selectVersion(versionId);
    closeDrawer();
  };

  const restoreVersion = async (versionId: string) => {
    await controller.restoreVersion(versionId);
    closeDrawer();
  };

  const assistantMessage = running
    ? 'Я собираю виджет и проверяю его на каждом этапе. Предпросмотр справа обновится автоматически.'
    : status === 'failed' || status === 'cancelled'
      ? 'Работа остановилась. Последняя сохранённая версия не потеряна — можно повторить запуск.'
      : 'Виджет готов. Посмотрите результат справа и напишите ниже, что хотите изменить.';

  return (
    <div className="studio-app studio-app--workbench">
      <header className="studio-header studio-header--workbench">
        <a className="studio-header__logo" href="/" aria-label="Kaigo — на главную">
          <KaigoLogo />
        </a>
        <div className="studio-header__session">
          <span>{domain}</span>
          <strong data-connection={controller.connection}>{controller.activityMessage}</strong>
        </div>
        <div className="studio-header__actions">
          <button type="button" onClick={() => setDrawer('projects')} aria-label="Открыть мои виджеты">
            <FolderOpen aria-hidden size={19} />
            <span className="studio-action-label">Проекты</span>
          </button>
          <button type="button" onClick={() => setDrawer('versions')} aria-label="Открыть версии">
            <ClockCounterClockwise aria-hidden size={19} />
            <span className="studio-action-label">Версии</span>
            {controller.versions.length > 0 && (
              <span className="studio-header__count">{controller.versions.length}</span>
            )}
          </button>
          <button type="button" onClick={() => setDrawer('account')} aria-label="Открыть тариф и лимиты">
            <UserCircle aria-hidden size={19} />
            <span className="studio-action-label">Аккаунт</span>
          </button>
          <button
            type="button"
            className="studio-header__publish"
            data-ready={freeResultReady ? 'true' : 'false'}
            onClick={() => setDrawer('publication')}
            aria-label="Открыть публикацию"
          >
            <CloudArrowUp aria-hidden size={19} />
            <span className="studio-action-label">Опубликовать</span>
          </button>
        </div>
      </header>

      <main className="studio-workbench" aria-label="Рабочая студия" data-mobile-pane={mobilePane}>
        <div className="studio-workbench__mobile-tabs" aria-label="Раздел студии">
          <button
            type="button"
            aria-pressed={mobilePane === 'chat'}
            onClick={() => setMobilePane('chat')}
          >
            Чат
          </button>
          <button
            type="button"
            aria-pressed={mobilePane === 'preview'}
            onClick={() => setMobilePane('preview')}
          >
            Предпросмотр
          </button>
        </div>

        <aside className="studio-conversation" aria-label="Чат с Kaigo">
          <header className="studio-conversation__header">
            <span className="studio-conversation__avatar" aria-hidden data-running={running ? 'true' : 'false'}>
              <span>K</span>
            </span>
            <div>
              <h1>Чат с Kaigo</h1>
              <p><span /> AI-дизайнер работает с вашим виджетом</p>
            </div>
          </header>

          <details className="studio-conversation__context">
            <summary>
              <span>
                <strong>Контекст проекта</strong>
                <small>{domain}</small>
              </span>
              <CaretDown aria-hidden size={17} />
            </summary>
            <div>
              <span>Ссылка на сайт</span>
              <p>{sourceUrl}</p>
              <span>Пожелание к AI-сотруднику</span>
              <p>{brief || 'Без дополнительных пожеланий'}</p>
            </div>
          </details>

          <div className="studio-conversation__feed">
            <div className="studio-message studio-message--assistant">
              <span className="studio-message__mark" aria-hidden>K</span>
              <p>{assistantMessage}</p>
            </div>

            {controller.connection === 'polling' && (
              <p className="studio-conversation__connection" role="status">
                Резервный режим обновления
              </p>
            )}

            {controller.runId && (
              <StudioProgress
                status={status}
                progress={snapshotProgress(controller)}
                currentStage={controller.snapshot?.current_stage}
                lastCompletedStage={controller.snapshot?.last_completed_stage}
                events={controller.events}
                activityFallback={controller.activityMessage}
              />
            )}

            {errorNotice}

            {running && (
              <button
                type="button"
                className="studio-conversation__secondary-action"
                onClick={() => void controller.cancelRun()}
                disabled={controller.mutationPending}
              >
                <StopCircle aria-hidden size={18} /> Отменить генерацию
              </button>
            )}
            {(status === 'failed' || status === 'cancelled') && (
              <button
                type="button"
                className="studio-conversation__secondary-action"
                onClick={() => void controller.retryRun()}
                disabled={controller.mutationPending}
              >
                <ArrowsClockwise aria-hidden size={18} /> Повторить запуск
              </button>
            )}

            <StudioTimeline events={controller.events} running={running} />

            {!running && isReadyQuality(qualityStatus) && (
              <div className="studio-message studio-message--assistant studio-message--ready">
                <CheckCircle aria-hidden size={18} weight="fill" />
                <p>Готово. Каждое новое пожелание сохранится отдельной версией.</p>
              </div>
            )}
          </div>

          <form className="studio-conversation__composer" onSubmit={submitRefinement}>
            <label className="sr-only" htmlFor="studio-refinement">Что изменить в виджете?</label>
            <div>
              <textarea
                id="studio-refinement"
                maxLength={2_000}
                placeholder={running ? 'Дождитесь завершения текущего этапа…' : 'Напишите, что изменить в виджете…'}
                value={refinement}
                onChange={(event) => setRefinement(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    event.key === 'Enter'
                    && (event.ctrlKey || event.metaKey)
                    && refinable
                    && !controller.mutationPending
                    && refinement.trim()
                  ) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
                disabled={!refinable || controller.mutationPending}
              />
              <button
                type="submit"
                disabled={!refinable || controller.mutationPending || !refinement.trim()}
                aria-label="Применить изменение"
              >
                <PaperPlaneTilt aria-hidden size={19} weight="fill" />
              </button>
            </div>
            <small>{refinable
              ? 'Каждая доработка сохранится отдельной версией — предыдущие варианты не потеряются.'
              : running
                ? 'Чат станет доступен, когда Kaigo закончит текущую версию.'
                : 'Доработка станет доступна после проверенной версии.'}</small>
          </form>
        </aside>

        <section className="studio-preview-pane" aria-label="Предпросмотр виджета">
          <StudioPreview
            runId={controller.previewRunId}
            revision={previewRevision}
            versionNumber={displayedVersionNumber}
            compactProjectHeader
            projectMode
            csrfToken={controller.csrfToken}
            artDirection={artifact?.art_direction ?? ''}
            qualityStatus={qualityStatus}
            viewport={viewport}
            onViewportChange={setViewport}
          />
        </section>
      </main>

      <StudioDrawer
        open={drawer === 'projects'}
        title="Мои виджеты"
        description="Откройте другой проект или начните новый."
        onClose={closeDrawer}
      >
        <StudioLibrary
          onOpenProject={(nextProjectId) => {
            closeDrawer();
            onOpenProject(nextProjectId);
          }}
          onCreateProject={() => {
            closeDrawer();
            onOpenStudioHome(true);
          }}
        />
        <button
          type="button"
          className="studio-drawer__primary"
          onClick={() => {
            closeDrawer();
            onOpenStudioHome(true);
          }}
        >
          <Plus aria-hidden size={18} weight="bold" /> Новый виджет
        </button>
      </StudioDrawer>

      <StudioDrawer
        open={drawer === 'versions'}
        title="История версий"
        description="Сравнивайте варианты и возвращайте подходящую версию."
        onClose={closeDrawer}
      >
        {controller.versionsAvailable && controller.versions.length > 0 ? (
          <ProjectVersionHistory
            versions={controller.versions}
            activeVersionId={activeVersionId}
            selectedVersionId={selectedVersion?.id ?? null}
            mutationPending={controller.mutationPending}
            running={running}
            onSelect={(versionId) => void selectVersion(versionId)}
            onRestore={(versionId) => void restoreVersion(versionId)}
          />
        ) : (
          <div className="studio-drawer__empty">
            <ClockCounterClockwise aria-hidden size={24} />
            <strong>История появится после первой готовой версии</strong>
            <p>Все последующие доработки будут сохраняться здесь автоматически.</p>
          </div>
        )}
      </StudioDrawer>

      <StudioDrawer
        open={drawer === 'account'}
        title="Тариф и лимиты"
        description="Подписка, продление и доступный объём доработок."
        onClose={closeDrawer}
      >
        <button
          type="button"
          className="studio-drawer__contact-launcher"
          onClick={() => setDrawer('contact')}
        >
          Помощь и обратная связь
        </button>
        <StudioAccountPanel onOpenPublication={() => setDrawer('publication')} />
      </StudioDrawer>

      <StudioDrawer
        open={drawer === 'contact'}
        title="Помощь и обратная связь"
        description="Вопрос, ошибка, идея или сотрудничество."
        onClose={closeDrawer}
      >
        <StudioContactPanel domain={domain} projectId={projectId} />
      </StudioDrawer>

      <StudioDrawer
        open={drawer === 'publication'}
        title="Публикация виджета"
        description="Подключение, тариф и код установки находятся в одном месте."
        onClose={closeDrawer}
      >
        {freeResultReady && (controller.versionsAvailable ? Boolean(selectedVersion) : Boolean(artifact?.id)) ? (
          <UpgradeGate
            csrfToken={controller.csrfToken}
            projectId={projectId}
            versionsEnabled={controller.versionsAvailable}
            projectVersionId={selectedVersion?.id}
            projectVersionOrdinal={selectedVersion?.ordinal}
            projectVersions={controller.versions}
            artifactId={artifact?.id}
            revision={artifact?.revision}
          />
        ) : (
          <div className="studio-drawer__empty">
            <CloudArrowUp aria-hidden size={24} />
            <strong>Публикация откроется после проверки</strong>
            <p>Пока Kaigo работает, можно следить за этапами в чате и смотреть обновления справа.</p>
          </div>
        )}
      </StudioDrawer>
    </div>
  );
}
