import { AnalysisSection } from './AnalysisSection';
import { CapabilitiesSection } from './CapabilitiesSection';
import { CaseStudySection } from './CaseStudySection';
import { FaqSection } from './FaqSection';
import { FinalCtaSection } from './FinalCtaSection';
import { FreeResultSection } from './FreeResultSection';
import { HeroSection } from './HeroSection';
import { HowItWorksSection } from './HowItWorksSection';
import { StudioSection } from './StudioSection';

export function LandingPage() {
  useEffect(() => {
    void ensureLandingJourney();
  }, []);

  return (
    <main>
      <HeroSection />
      <FreeResultSection />
      <HowItWorksSection />
      <AnalysisSection />
      <CaseStudySection />
      <CapabilitiesSection />
      <StudioSection />
      <FaqSection />
      <FinalCtaSection />
    </main>
  );
}
import { useEffect } from 'react';

import { ensureLandingJourney } from '../shared/journey';
