import { motion, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';

type RevealProps = {
  children: ReactNode;
  className?: string;
  delay?: number;
  preset?: RevealPreset;
};

type RevealPreset = 'default' | 'heading' | 'fromLeft' | 'fromRight' | 'scale';

const hiddenByPreset = {
  default: { opacity: 0, y: 28 },
  heading: { opacity: 0, y: 22 },
  fromLeft: { opacity: 0, x: 'clamp(-65px, -5vw, -36px)', scale: 0.92 },
  fromRight: { opacity: 0, x: 'clamp(36px, 5vw, 65px)', scale: 0.92 },
  scale: { opacity: 0, scale: 0.9 },
} as const;

const visibleByPreset = {
  default: { opacity: 1, y: 0 },
  heading: { opacity: 1, y: 0 },
  fromLeft: { opacity: 1, x: 0, scale: 1 },
  fromRight: { opacity: 1, x: 0, scale: 1 },
  scale: { opacity: 1, scale: 1 },
} as const;

export function Reveal({ children, className, delay = 0, preset = 'default' }: RevealProps) {
  const reducedMotion = useReducedMotion();
  const viewportMotionAvailable = import.meta.env.MODE !== 'test'
    && typeof IntersectionObserver !== 'undefined';
  const transition = reducedMotion
    ? { duration: 0 }
    : preset === 'heading'
      ? { duration: 0.6, ease: [0.16, 1, 0.3, 1] as const, delay }
      : { type: 'spring' as const, stiffness: 108, damping: 15, mass: 0.84, delay };

  return (
    <motion.div
      className={className}
      initial={reducedMotion || !viewportMotionAvailable ? false : hiddenByPreset[preset]}
      whileInView={visibleByPreset[preset]}
      viewport={{ once: true, amount: 0.22 }}
      transition={transition}
    >
      {children}
    </motion.div>
  );
}
