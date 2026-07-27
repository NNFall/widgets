import { useInView, useReducedMotion } from 'motion/react';
import { useEffect, useRef, useState } from 'react';
import type { HTMLAttributes, ReactNode, RefObject } from 'react';

type MotionActivityState<T extends HTMLElement> = {
  active: boolean;
  ref: RefObject<T | null>;
};

export function useMotionActivity<T extends HTMLElement = HTMLDivElement>(): MotionActivityState<T> {
  const ref = useRef<T>(null);
  const inViewport = useInView(ref, { amount: 0.18 });
  const reducedMotion = Boolean(useReducedMotion());
  const [documentVisible, setDocumentVisible] = useState(
    () => typeof document === 'undefined' || document.visibilityState === 'visible',
  );

  useEffect(() => {
    const syncVisibility = () => setDocumentVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', syncVisibility);
    return () => document.removeEventListener('visibilitychange', syncVisibility);
  }, []);

  return {
    active: inViewport && documentVisible && !reducedMotion,
    ref,
  };
}

type MotionActivityProps = Omit<HTMLAttributes<HTMLDivElement>, 'children'> & {
  children: ReactNode | ((active: boolean) => ReactNode);
};

export function MotionActivity({ children, ...props }: MotionActivityProps) {
  const { active, ref } = useMotionActivity<HTMLDivElement>();

  return (
    <div {...props} ref={ref} data-motion-active={active ? 'true' : 'false'}>
      {typeof children === 'function' ? children(active) : children}
    </div>
  );
}
