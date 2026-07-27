type KaigoLogoProps = {
  className?: string;
};

export function KaigoLogo({ className = '' }: KaigoLogoProps) {
  return (
    <span className={`kaigo-logo ${className}`.trim()} role="img" aria-label="Kaigo">
      <svg className="kaigo-logo__mark" viewBox="0 0 42 42" aria-hidden="true">
        <defs>
          <linearGradient id="kaigo-mark-gradient" x1="3" y1="39" x2="37" y2="3">
            <stop offset="0" stopColor="#11ad94" />
            <stop offset="1" stopColor="#7bc8a8" />
          </linearGradient>
        </defs>
        <path d="M4 4h13v34H4z" rx="3" fill="url(#kaigo-mark-gradient)" />
        <path d="M21 4h4c8.3 0 13 4.2 13 10.1S33.3 24 25 24h-4V4Z" fill="url(#kaigo-mark-gradient)" />
        <path d="M21 26h4c7 0 11 3 13 12H21V26Z" fill="url(#kaigo-mark-gradient)" />
      </svg>
      <span className="kaigo-logo__wordmark">Kaigo</span>
    </span>
  );
}
