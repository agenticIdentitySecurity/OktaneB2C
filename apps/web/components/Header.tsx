'use client';

import { useEffect, useState } from 'react';
import { ShoppingCart, ShieldCheck, User, LogOut, ChevronDown } from 'lucide-react';
import type { Profile } from '@/lib/types';

export default function Header({
  profile,
  shoppers,
  onSignIn,
  onSignOut,
}: {
  profile: Profile | null;
  shoppers: Profile[];
  onSignIn: (email: string) => void;
  onSignOut: () => void;
}) {
  const [open, setOpen] = useState(false);
  // demo_mode from the agent — 'mock' shows the shopper picker; anything else
  // (real Okta) redirects the browser to /auth/login for the OIDC dance.
  const [demoMode, setDemoMode] = useState<string | null>(null);
  useEffect(() => {
    fetch('/api/whoami')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setDemoMode(d.demo_mode))
      .catch(() => {});
  }, []);
  const isOkta = demoMode !== null && demoMode !== 'mock';

  return (
    <header className="sticky top-0 z-30 border-b border-neutral-border bg-neutral-bg/85 backdrop-blur-md">
      <div className="mx-auto flex max-w-7xl items-center gap-4 px-6 py-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent">
            <span className="font-display text-lg font-extrabold text-neutral-bg">
              C
            </span>
          </div>
          <div className="leading-none">
            <div className="font-display text-lg font-extrabold tracking-tight">
              Court<span className="text-accent">Edge</span>
            </div>
            <div className="mt-0.5 text-[10px] uppercase tracking-[0.18em] text-net-white/40">
              Basketball Gear
            </div>
          </div>
        </div>

        <nav className="ml-6 hidden items-center gap-6 text-sm text-net-white/60 md:flex">
          {['Basketballs', 'Footwear', 'Hoops', 'Apparel'].map((item) => (
            <span
              key={item}
              className="cursor-default transition-colors hover:text-net-white"
            >
              {item}
            </span>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <div className="hidden items-center gap-1.5 rounded-full border border-okta-blue/30 bg-okta-blue/10 px-3 py-1.5 text-xs text-okta-blue-light sm:flex">
            <ShieldCheck className="h-3.5 w-3.5" />
            Secured by Okta
          </div>
          <button
            type="button"
            className="relative rounded-lg border border-neutral-border p-2 text-net-white/70 hover:border-accent/50 hover:text-accent"
            aria-label="Cart"
          >
            <ShoppingCart className="h-4 w-4" />
          </button>

          {profile ? (
            <div className="flex items-center gap-2">
              <div className="hidden text-right leading-tight sm:block">
                <div className="text-xs font-medium">{profile.name}</div>
                <div className="font-mono text-[10px] text-net-white/35">
                  {profile.sub}
                </div>
              </div>
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent/20 text-xs font-bold text-accent">
                {profile.name.slice(0, 1).toUpperCase()}
              </div>
              <button
                type="button"
                onClick={onSignOut}
                className="rounded-lg border border-neutral-border p-2 text-net-white/60 hover:border-error-red/50 hover:text-error-red"
                aria-label="Sign out"
              >
                <LogOut className="h-4 w-4" />
              </button>
            </div>
          ) : isOkta ? (
            <a
              href="/auth/login?return_to=/"
              className="flex items-center gap-2 rounded-lg border border-okta-blue/40 bg-okta-blue/10 px-3 py-2 text-sm font-medium text-okta-blue-light hover:bg-okta-blue/20"
            >
              <User className="h-4 w-4" />
              <span className="hidden sm:inline">Sign in with Okta</span>
            </a>
          ) : (
            <div className="relative">
              <button
                type="button"
                onClick={() => setOpen(!open)}
                className="flex items-center gap-2 rounded-lg border border-neutral-border px-3 py-2 text-sm text-net-white/80 hover:border-accent/50 hover:text-accent"
              >
                <User className="h-4 w-4" />
                <span className="hidden sm:inline">Sign in</span>
                <ChevronDown className="h-3 w-3 opacity-60" />
              </button>

              {open && (
                <div className="absolute right-0 z-40 mt-2 w-64 overflow-hidden rounded-xl border border-neutral-border bg-primary shadow-xl">
                  <div className="border-b border-neutral-border px-3 py-2 text-[10px] uppercase tracking-[0.14em] text-net-white/35">
                    Continue with Okta
                  </div>
                  {shoppers.map((shopper) => (
                    <button
                      key={shopper.sub}
                      type="button"
                      onClick={() => {
                        setOpen(false);
                        onSignIn(shopper.email);
                      }}
                      className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left hover:bg-accent/10"
                    >
                      <div className="flex h-7 w-7 items-center justify-center rounded-full bg-okta-blue/20 text-[11px] font-bold text-okta-blue-light">
                        {shopper.name.slice(0, 1).toUpperCase()}
                      </div>
                      <div className="leading-tight">
                        <div className="text-xs font-medium">{shopper.name}</div>
                        <div className="text-[10px] text-net-white/40">
                          {shopper.email}
                        </div>
                      </div>
                    </button>
                  ))}
                  {shoppers.length === 0 && (
                    <div className="px-3 py-3 text-xs text-net-white/40">
                      No demo shoppers available — is the agent service running?
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
