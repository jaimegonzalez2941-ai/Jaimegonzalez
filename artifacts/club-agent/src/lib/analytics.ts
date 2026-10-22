type AnalyticsData = Record<string, string | number | boolean>;

declare global {
  interface Window {
    umami?: {
      track(name: string, data?: AnalyticsData): void | Promise<unknown>;
    };
  }
}

export type InstallLocation = 'landing_hero' | 'landing_footer' | 'start_here_guide';
export type ConnectionLocation = 'staff_workspace' | 'player_development';

// Callers select a fixed event, never supply URLs, identities, codes or records.
type OnboardingEvent =
  | { name: 'install_link_clicked'; location: InstallLocation }
  | { name: 'start_here_entered'; location: 'start_here_guide' }
  | { name: 'account_connection_succeeded'; location: ConnectionLocation };

export function trackEvent(event: OnboardingEvent): void {
  if (typeof window === 'undefined') return;
  try {
    const data: AnalyticsData = { location: event.location };
    if (event.name === 'account_connection_succeeded') data.outcome = 'success';
    // Do not spread caller data: only these fixed dimensions may be sent.
    void Promise.resolve(window.umami?.track(event.name, data)).catch(() => {
      // A rejected tracker request must not affect onboarding.
    });
  } catch {
    // A missing or broken tracker must not affect onboarding.
  }
}

// One observer per website-account session, shared by staff and player routes.
// Observe only successful server responses; an existing link is not a new one.
export function createConnectionObserver() {
  let previous: boolean | undefined;
  return (linked: boolean, location: ConnectionLocation) => {
    if (previous === false && linked) {
      trackEvent({ name: 'account_connection_succeeded', location });
    }
    previous = linked;
  };
}
