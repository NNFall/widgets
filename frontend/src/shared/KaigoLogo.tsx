type KaigoLogoProps = {
  className?: string;
  tone?: 'mint' | 'coral';
};

export function KaigoLogo({ className = '', tone = 'mint' }: KaigoLogoProps) {
  return (
    <span className={`kaigo-logo ${className}`.trim()} role="img" aria-label="Kaigo" data-tone={tone}>
      <img
        className="kaigo-logo__mark"
        src="/assets/brand/kaigo-living-fold-mark.png"
        alt=""
        aria-hidden="true"
        data-kaigo-mark="living-fold"
        draggable={false}
      />
      <span className="kaigo-logo__wordmark">Kaigo</span>
    </span>
  );
}
