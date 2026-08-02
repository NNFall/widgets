import { AuthGate } from '../auth/AuthGate';
import { StudioPage } from './StudioPage';

export function StudioRoute() {
  return (
    <AuthGate>
      <StudioPage />
    </AuthGate>
  );
}
