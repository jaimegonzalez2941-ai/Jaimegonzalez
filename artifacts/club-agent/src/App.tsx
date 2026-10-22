import { type ReactNode, useEffect, useMemo, lazy, Suspense } from 'react';
import { ClerkProvider, useAuth } from '@clerk/react';
import { publishableKeyFromHost } from '@clerk/react/internal';
import { shadcn } from '@clerk/themes';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ErrorBoundary } from '@/components/error-boundary';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import NotFound from '@/pages/not-found';
import Landing from '@/pages/landing';
import { RouteMetadata } from '@/components/route-metadata';
import { Route, Switch, useLocation, Router as WouterRouter } from 'wouter';

const Guide = lazy(() => import('@/pages/guide'));
const AdminPage = lazy(() => import('@/pages/admin'));
const DevelopmentPage = lazy(() => import('@/pages/development'));
const SignInPage = lazy(() => import('@/pages/auth').then((module) => ({ default: module.SignInPage })));
const SignUpPage = lazy(() => import('@/pages/auth').then((module) => ({ default: module.SignUpPage })));

const clerkPubKey = publishableKeyFromHost(
  window.location.hostname,
  import.meta.env.VITE_CLERK_PUBLISHABLE_KEY,
);
const clerkProxyUrl = import.meta.env.VITE_CLERK_PROXY_URL;
const basePath = import.meta.env.BASE_URL.replace(/\/$/, '');

function stripBase(path: string): string {
  return basePath && path.startsWith(basePath) ? path.slice(basePath.length) || '/' : path;
}

if (!clerkPubKey) {
  throw new Error('Missing VITE_CLERK_PUBLISHABLE_KEY');
}

const clerkAppearance = {
  theme: shadcn,
  cssLayerName: 'clerk',
  options: {
    logoPlacement: 'inside' as const,
    logoLinkUrl: basePath || '/',
    logoImageUrl: `${window.location.origin}${basePath}/logo.svg`,
  },
  variables: {
    colorPrimary: '#1a1f4d',
    colorForeground: '#12163a',
    colorMutedForeground: '#4b5072',
    colorBackground: '#fbf8ef',
    colorInput: '#fffdf7',
    colorInputForeground: '#12163a',
    colorDanger: '#a3281c',
    colorNeutral: '#12163a',
    fontFamily: "'Figtree', system-ui, sans-serif",
    borderRadius: '0.5rem',
  },
  elements: {
    cardBox: 'w-[420px] max-w-full bg-[#fbf8ef] border border-[#d9d0b8] shadow-xl rounded-2xl',
    card: '!bg-transparent',
    footer: '!bg-transparent',
    headerTitle: 'text-[#12163a] font-bold',
    headerSubtitle: 'text-[#4b5072]',
    socialButtonsBlockButton: 'border border-[#c9bfa3] bg-white text-[#12163a]',
    socialButtonsBlockButtonText: 'text-[#12163a] font-medium',
    formFieldLabel: 'text-[#12163a] font-medium',
    formFieldInput: 'bg-white border border-[#b9ae90] text-[#12163a]',
    formButtonPrimary: 'bg-[#1a1f4d] text-[#f6e7b4] hover:bg-[#12163a]',
    footerActionText: 'text-[#4b5072]',
    footerActionLink: 'text-[#1a1f4d] font-semibold underline',
    dividerLine: 'bg-[#d9d0b8]',
    dividerText: 'text-[#4b5072]',
    alert: 'text-[#12163a]',
    otpCodeFieldInput: 'border border-[#b9ae90] text-[#12163a]',
    formFieldRow: 'text-[#12163a]',
    main: 'text-[#12163a]',
  },
};

function SessionQueries({ children }: { children: ReactNode }) {
  const { userId } = useAuth();
  // A new cache and mounted workspace per verified account: never render a
  // previous user's saved evidence while a session-change effect is pending.
  const client = useMemo(() => new QueryClient({
    defaultOptions: { queries: { refetchOnWindowFocus: false } },
  }), [userId]);
  useEffect(() => {
    return () => { void client.cancelQueries(); client.clear(); };
  }, [client]);
  return <QueryClientProvider key={userId ?? 'signed-out'} client={client}>{children}</QueryClientProvider>;
}

function RoutedErrorBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}

function ClerkProviderWithRoutes() {
  const [, setLocation] = useLocation();
  return (
    <ClerkProvider
      publishableKey={clerkPubKey}
      proxyUrl={clerkProxyUrl}
      appearance={clerkAppearance}
      signInUrl={`${basePath}/sign-in`}
      signUpUrl={`${basePath}/sign-up`}
      localization={{
        signIn: { start: { title: 'Sign in to Club Agent', subtitle: 'Your account links to Discord after sign in' } },
        signUp: { start: { title: 'Create your account', subtitle: 'Registration does not grant club access' } },
      }}
      routerPush={(to) => setLocation(stripBase(to))}
      routerReplace={(to) => setLocation(stripBase(to), { replace: true })}
    >
      <SessionQueries>
        <TooltipProvider>
          <RouteMetadata />
          <RoutedErrorBoundary>
            <Suspense fallback={<div className="grid min-h-[70dvh] place-items-center bg-background p-8 text-foreground" role="status">Loading Club Agent…</div>}>
            <Switch>
              <Route path="/" component={Landing} />
              <Route path="/guide" component={Guide} />
              <Route path="/sign-in/*?" component={SignInPage} />
              <Route path="/sign-up/*?" component={SignUpPage} />
              <Route path="/development" component={DevelopmentPage} />
              <Route path="/admin" component={AdminPage} />
              <Route path="/admin/:screen" component={AdminPage} />
              <Route component={NotFound} />
            </Switch>
            </Suspense>
          </RoutedErrorBoundary>
          <Toaster />
        </TooltipProvider>
      </SessionQueries>
    </ClerkProvider>
  );
}

function App() {
  return (
    <WouterRouter base={basePath}>
      <ClerkProviderWithRoutes />
    </WouterRouter>
  );
}

export default App;
