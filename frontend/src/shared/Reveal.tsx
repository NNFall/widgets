import { motion, useReducedMotion } from 'motion/react';
import type { ReactNode } from 'react';

type RevealProps = {
  children: ReactNode;
  className?: string;
  delay?: number;
};

export function Reveal({ children, className, delay = 0 }: RevealProps) {
  const reducedMotion = useReducedMotion();

  return (
    <motion.div
      className={className}
      initial={reducedMotion ? false : { opacity: 1, y: 28 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.22 }}
      transition={reducedMotion ? { duration: 0 } : {
        type: 'spring', stiffness: 88, damping: 20, delay,
      }}
    >
      {children}
    </motion.div>
  );
}
