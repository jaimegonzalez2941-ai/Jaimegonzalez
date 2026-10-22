import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { createConnectionObserver, trackEvent } from './analytics';

afterEach(() => {
  Reflect.deleteProperty(globalThis, 'window');
});

function captureEvents() {
  const events: unknown[][] = [];
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { umami: { track: (...args: unknown[]) => { events.push(args); } } },
  });
  return events;
}

test('events contain only fixed locations/outcomes, never caller records', () => {
  const events = captureEvents();
  for (const location of ['landing_hero', 'landing_footer', 'start_here_guide'] as const) {
    trackEvent({ name: 'install_link_clicked', location });
  }
  trackEvent({ name: 'start_here_entered', location: 'start_here_guide' });
  const eventWithExtraData = {
    name: 'account_connection_succeeded' as const,
    location: 'staff_workspace' as const,
    code: 'PRIVATE_TEST_CODE',
    discordId: 'PRIVATE_TEST_ID',
    evidence: 'PRIVATE_TEST_EVIDENCE',
  };
  trackEvent(eventWithExtraData);
  assert.deepEqual(events, [
    ['install_link_clicked', { location: 'landing_hero' }],
    ['install_link_clicked', { location: 'landing_footer' }],
    ['install_link_clicked', { location: 'start_here_guide' }],
    ['start_here_entered', { location: 'start_here_guide' }],
    ['account_connection_succeeded', { location: 'staff_workspace', outcome: 'success' }],
  ]);
});

test('missing, throwing and rejecting trackers do not break onboarding', async () => {
  const event = { name: 'start_here_entered', location: 'start_here_guide' } as const;
  assert.doesNotThrow(() => trackEvent(event)); // Server rendering.
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {} });
  assert.doesNotThrow(() => trackEvent(event));
  window.umami = { track: () => { throw new Error('Tracker unavailable'); } };
  assert.doesNotThrow(() => trackEvent(event));
  window.umami = { track: () => Promise.reject(new Error('Network unavailable')) };
  trackEvent(event);
  await new Promise((resolve) => setImmediate(resolve));
});

test('existing links and repeated refreshes are not new connections', () => {
  const events = captureEvents();
  const existing = createConnectionObserver();
  existing(true, 'staff_workspace');
  existing(true, 'player_development');
  assert.deepEqual(events, []);

  const onboarding = createConnectionObserver();
  onboarding(false, 'staff_workspace');
  onboarding(false, 'staff_workspace');
  onboarding(true, 'player_development');
  onboarding(true, 'staff_workspace');
  onboarding(true, 'player_development');
  assert.deepEqual(events, [
    ['account_connection_succeeded', { location: 'player_development', outcome: 'success' }],
  ]);
});

test('a different account does not inherit the previous account connection state', () => {
  const events = captureEvents();
  const firstAccount = createConnectionObserver();
  firstAccount(false, 'staff_workspace');
  const secondAccount = createConnectionObserver();
  secondAccount(true, 'staff_workspace');
  assert.deepEqual(events, []);
});
