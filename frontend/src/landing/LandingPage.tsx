import { AnalysisSection } from './AnalysisSection';
import { CapabilitiesSection } from './CapabilitiesSection';
import { CaseStudySection } from './CaseStudySection';
import { FaqSection } from './FaqSection';
import { FinalCtaSection } from './FinalCtaSection';
import { HeroSection } from './HeroSection';
import { HowItWorksSection } from './HowItWorksSection';
import { StudioSection } from './StudioSection';

export function LandingPage() {
  return (
    <main>
      <HeroSection />
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
