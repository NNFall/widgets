import { ArrowRight, Sparkle } from '@phosphor-icons/react';

export function UpgradeGate() {
  return (
    <aside className="studio-upgrade" aria-labelledby="studio-upgrade-title">
      <Sparkle aria-hidden size={22} weight="fill" />
      <div>
        <h2 id="studio-upgrade-title">Бесплатный результат готов</h2>
        <p>Он останется доступен в проекте. Для дальнейшей доработки и публикации понадобится тариф.</p>
      </div>
      <button type="button" disabled aria-describedby="studio-upgrade-title">
        Доработать и опубликовать <ArrowRight aria-hidden size={18} />
      </button>
    </aside>
  );
}
