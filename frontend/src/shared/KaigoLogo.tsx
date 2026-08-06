import { useId } from 'react';

type KaigoLogoProps = {
  className?: string;
  tone?: 'mint' | 'coral';
};

export function KaigoLogo({ className = '', tone = 'mint' }: KaigoLogoProps) {
  const sanitizedId = useId().replace(/[^A-Za-z0-9_-]/g, '');
  const gradientId = `${sanitizedId}-kaigo-mark-gradient`;
  const gradient = tone === 'coral'
    ? ['#ff8a55', '#fe4d1d']
    : ['#11ad94', '#7bc8a8'];

  return (
    <span className={`kaigo-logo ${className}`.trim()} role="img" aria-label="Kaigo" data-tone={tone}>
      <svg
        className="kaigo-logo__mark"
        viewBox="0 0 42 42"
        aria-hidden="true"
        data-kaigo-mark="K"
      >
        <defs>
          <linearGradient id={gradientId} x1="3" y1="39" x2="37" y2="3">
            <stop offset="0" stopColor={gradient[0]} />
            <stop offset="1" stopColor={gradient[1]} />
          </linearGradient>
        </defs>
        <path
          d="M4 4h9v34H4z"
          data-kaigo-stroke="stem"
          fill={`url(#${gradientId})`}
        />
        <path
          d="M13 21 29 4h11L22 22z"
          data-kaigo-stroke="upper-diagonal"
          fill={`url(#${gradientId})`}
        />
        <path
          d="M13 21h9l18 17H29z"
          data-kaigo-stroke="lower-diagonal"
          fill={`url(#${gradientId})`}
        />
      </svg>
      <span className="kaigo-logo__wordmark">Kaigo</span>
    </span>
  );
}
