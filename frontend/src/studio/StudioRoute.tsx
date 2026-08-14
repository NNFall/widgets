import { AuthGate } from '../auth/AuthGate';
import { PresentationGate } from './PresentationGate';
import { StudioPage } from './StudioPage';

export function StudioRoute() {
  return (
    <AuthGate>
      <PresentationGate>
        <StudioPage />
      </PresentationGate>
    </AuthGate>
  );
}
