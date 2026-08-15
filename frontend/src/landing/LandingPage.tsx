import { useEffect } from 'react';

import { ensureLandingJourney } from '../shared/journey';
import { AnalysisSection } from './AnalysisSection';
import { CapabilitiesSection } from './CapabilitiesSection';
import { CaseStudySection } from './CaseStudySection';
import { ContactSection } from './ContactSection';
import { FaqSection } from './FaqSection';
import { FinalCtaSection } from './FinalCtaSection';
import { FreeResultSection } from './FreeResultSection';
import { HeroSection } from './HeroSection';
import { ProductTour } from './ProductTour';
import { SiteFooter } from './SiteFooter';
import { StudioSection } from './StudioSection';

export function LandingPage() {
  useEffect(() => {
    void ensureLandingJourney();
  }, []);

  return (
    <main className="landing-page">
      <HeroSection />
      <ProductTour />
      <FreeResultSection />
      <AnalysisSection />
      <CaseStudySection />
      <CapabilitiesSection />
      <StudioSection />
      <FaqSection />
      <ContactSection />
      <FinalCtaSection />
      <SiteFooter />
    </main>
  );
}
