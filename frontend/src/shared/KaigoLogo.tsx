type KaigoLogoProps = {
  className?: string;
};

export function KaigoLogo({ className = '' }: KaigoLogoProps) {
  return (
    <span className={`kaigo-logo ${className}`.trim()} role="img" aria-label="Kaigo">
      <svg
        className="kaigo-logo__mark"
        viewBox="0 0 42 42"
        aria-hidden="true"
        data-kaigo-mark="K"
      >
        <defs>
          <linearGradient id="kaigo-mark-gradient" x1="3" y1="39" x2="37" y2="3">
            <stop offset="0" stopColor="#11ad94" />
            <stop offset="1" stopColor="#7bc8a8" />
          </linearGradient>
        </defs>
        <path
          d="M4 4h9v34H4z"
          data-kaigo-stroke="stem"
          fill="url(#kaigo-mark-gradient)"
        />
        <path
          d="M13 21 29 4h11L22 22z"
          data-kaigo-stroke="upper-diagonal"
          fill="url(#kaigo-mark-gradient)"
        />
        <path
          d="M13 21h9l18 17H29z"
          data-kaigo-stroke="lower-diagonal"
          fill="url(#kaigo-mark-gradient)"
        />
      </svg>
      <span className="kaigo-logo__wordmark">Kaigo</span>
    </span>
  );
}
