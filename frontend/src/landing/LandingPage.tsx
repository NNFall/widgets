import { type MouseEvent, useEffect } from 'react';

import { ensureLandingJourney, recordJourneyEvent } from '../shared/journey';
import { AnalysisSection } from './AnalysisSection';
import { CapabilitiesSection } from './CapabilitiesSection';
import { CaseStudySection } from './CaseStudySection';
import { FaqSection } from './FaqSection';
import { FinalCtaSection } from './FinalCtaSection';
import { FreeResultSection } from './FreeResultSection';
import { HeroSection } from './HeroSection';
import { ProductTour } from './ProductTour';
import { StudioSection } from './StudioSection';

export function LandingPage() {
  useEffect(() => {
    void ensureLandingJourney();
    const scrollEnd = document.getElementById('landing-scroll-end');
    if (!scrollEnd || typeof IntersectionObserver === 'undefined') return undefined;
    let recorded = false;
    const observer = new IntersectionObserver((entries) => {
      if (
        recorded
        || !entries.some((entry) => entry.isIntersecting && entry.intersectionRatio >= 1)
      ) return;
      recorded = true;
      observer.disconnect();
      void recordJourneyEvent('landing_scrolled_end');
    }, { threshold: 1 });
    observer.observe(scrollEnd);
    return () => observer.disconnect();
  }, []);

  const recordStudioClick = (event: MouseEvent<HTMLElement>) => {
    if (!(event.target instanceof Element)) return;
    const link = event.target.closest<HTMLAnchorElement>('a[href]');
    if (!link) return;
    const destination = new URL(link.href, window.location.href);
    if (
      destination.origin === window.location.origin
      && destination.pathname.replace(/\/+$/, '') === '/studio'
    ) {
      void recordJourneyEvent('studio_cta_clicked');
    }
  };

  return (
    <main className="landing-page" onClickCapture={recordStudioClick}>
      <HeroSection />
      <ProductTour />
      <FreeResultSection />
      <AnalysisSection />
      <CaseStudySection />
      <CapabilitiesSection />
      <StudioSection />
      <FaqSection />
      <FinalCtaSection />
    </main>
  );
}
